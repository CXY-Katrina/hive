"""Single-attempt preparation of the supported public Quay image repository."""
from datetime import timedelta
import re
import shlex

from .domain import DomainError, now


QUAY_TAG = re.compile(r'quay\.io/ascend/vllm-ascend:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}')
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}')


def pull_pending(state):
    attempt = state.get('image_pull', {})
    return attempt.get('status') == 'STARTING' and attempt.get('deadline', '9999') > now().isoformat()


def resolve_image(transport, node, image, boot_id, state, save):
    """Inspect locally; pull an allowlisted missing tag once with durable intent."""
    guard = 'test "$(cat /proc/sys/kernel/random/boot_id)" = ' + shlex.quote(boot_id) + '\n'
    probe = ('# HIVE_IMAGE_LOCAL\nset -eu\n' + guard
             + 'if image_id=$(docker image inspect --format ' + shlex.quote('{{.Id}}')
             + ' -- ' + shlex.quote(image) + ' 2>/dev/null); then\n'
             + 'printf "HIVE_IMAGE %s\\n" "$image_id"\n'
             + 'elif docker info --format ' + shlex.quote('{{.ServerVersion}}') + ' >/dev/null 2>&1; then\n'
             + 'printf "HIVE_IMAGE_MISSING\\n"\nelse printf "HIVE_IMAGE_UNAVAILABLE\\n"\nfi\n' + guard)

    def local():
        result = transport.run(node, probe, timeout=30)
        if result.code:
            raise DomainError('本地镜像状态或节点身份暂时无法确认',503)
        output = result.stdout.strip()
        if output == 'HIVE_IMAGE_MISSING':
            return None
        if output.startswith('HIVE_IMAGE ') and IMAGE_ID.fullmatch(output[11:]):
            return output[11:]
        raise DomainError('Docker 不可用或镜像核验结果不完整',503)

    digest = local()
    attempt = state.get('image_pull')
    if digest:
        if attempt and attempt.get('image') == image:
            attempt.update(status='COMPLETED', image_id=digest)
            save()
        return digest
    if not QUAY_TAG.fullmatch(image):
        raise DomainError('镜像本地不存在；仅支持自动拉取 quay.io/ascend/vllm-ascend:<tag>，其他镜像请先在节点加载',422)
    if attempt:
        if attempt.get('image') != image:
            raise DomainError('镜像准备来源与已保存的拉取意图不一致',422)
        if pull_pending(state):
            raise DomainError('镜像拉取结果尚未确认，等待最多 10 分钟加安全缓冲；不会重复拉取',503)
        raise DomainError('镜像拉取已结束或超过时限，节点仍无镜像；本次环境不会自动重试',422)
    # Queue wait, TCP connect, banner, authentication and channel opening can each
    # consume ssh_timeout before the bounded command begins. Include all of them.
    connection_timeout = getattr(getattr(transport,'settings',None),'ssh_timeout',20)
    deadline_seconds = 640 + 5 * connection_timeout + 30
    attempt = {'image':image,'status':'STARTING','deadline':(now()+timedelta(seconds=deadline_seconds)).isoformat()}
    state['image_pull'] = attempt
    save()  # Persist before SSH; a restart must never replay an uncertain pull.
    script = ('# HIVE_IMAGE_PULL\nset -eu\n' + guard
              + 'set +e\ntimeout --signal=TERM --kill-after=10s 600s docker pull -- '
              + shlex.quote(image) + ' >/dev/null 2>&1\ncode=$?\nset -e\n'
              + guard + 'printf "HIVE_IMAGE_PULL_EXIT %s\\n" "$code"\n')
    try:
        result = transport.run(node,script,timeout=640)
    except Exception:
        raise DomainError('镜像拉取连接中断，保留准备状态直到截止时间，不自动重试',503) from None
    match = re.fullmatch(r'HIVE_IMAGE_PULL_EXIT ([0-9]{1,3})',result.stdout.strip())
    if result.code or not match:
        raise DomainError('镜像拉取完成状态尚未确认，截止前保留资源且不自动重试',503)
    attempt.update(status='COMPLETED',exit_code=int(match[1]))
    save()
    if attempt['exit_code']:
        raise DomainError('镜像拉取失败或达到 10 分钟时限（退出码 '+match[1]+'）；请检查镜像标签和节点网络，本次不重试',422)
    digest = local()
    if not digest:
        raise DomainError('镜像拉取返回成功但本地仍无法找到镜像',422)
    attempt['image_id'] = digest
    save()
    return digest
