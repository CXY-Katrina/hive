import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** Portal positioning keeps metric descriptions outside scrolling table bounds. */
export function MetricHelp({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const cancelClose = () => { clearTimeout(closeTimer.current); };
  const show = () => { cancelClose(); setOpen(true); };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => {
      if (document.activeElement !== trigger.current && !popup.current?.matches(':hover')) setOpen(false);
    }, 180);
  };
  useEffect(() => () => clearTimeout(closeTimer.current), []);
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchor = trigger.current?.getBoundingClientRect();
      const box = popup.current?.getBoundingClientRect();
      if (!anchor || !box) return;
      const below = anchor.bottom + 10;
      const top = below + box.height <= window.innerHeight - 12 ? below : Math.max(12, anchor.top - box.height - 10);
      setPosition({ top, left: Math.max(12, Math.min(anchor.left, window.innerWidth - box.width - 12)) });
    };
    const dismiss = (event: KeyboardEvent) => { if (event.key === 'Escape') { cancelClose(); setOpen(false); } };
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    document.addEventListener('keydown', dismiss);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      document.removeEventListener('keydown', dismiss);
    };
  }, [open]);
  return <><button ref={trigger} type="button" className="metric-help-trigger" aria-label={label} aria-describedby={open ? id : undefined} onMouseEnter={show} onMouseLeave={scheduleClose} onFocus={show} onBlur={scheduleClose} onClick={show}>?</button>{open && createPortal(<div ref={popup} id={id} role="tooltip" className="metric-help-popup" style={position} onMouseEnter={cancelClose} onMouseLeave={scheduleClose}>{children}</div>, document.body)}</>;
}

export function HardwareMetricHelp() {
  return <MetricHelp label="查看集群与规格的指标查询方式"><strong>指标查询方式</strong><dl>
    <dt>NPU 代际</dt><dd>纳管时按机器规格选择 A2 / A3 / A5。</dd>
    <dt>服务器机型</dt><dd><code>cat /sys/class/dmi/id/product_name</code>；也支持纳管时填写。页面省略板卡产品后缀。</dd>
    <dt>SoC version</dt><dd><code>npu-smi info -t board -i &lt;NPU ID&gt; -c &lt;Chip ID&gt;</code>，读取 Chip Name / NPU Name。</dd>
    <dt>实测算力</dt><dd><code>ascend-dmi -f -t fp16 -d &lt;Device ID&gt; --et 10 --fmt normal -q</code> 逐个逻辑设备测量 FP16 算力，显示各卡最小值–最大值，单位 TFLOPS。管理员在整机空闲时手动测试；详情中的手动理论规格来自资料，按双芯模块计，与实测口径不同。</dd>
    <dt>每卡显存</dt><dd><code>npu-smi info</code> 中 HBM / Memory-Usage 总量，按 MiB 换算为 GiB；“卡”指平台可分配的逻辑设备。</dd>
    <dt>CPU 架构</dt><dd><code>uname -a</code> 保留系统原始信息；<code>uname -m</code> 判定 ARM64 / ARM / x86_64 / x86，详情可查看原始结果。</dd>
    <dt>HDK 版本</dt><dd><code>cat /usr/local/Ascend/driver/version.info</code>，优先读取 package_version，旧版本回退 Version / DriverVersion / version；表示 HDK 驱动软件包发布版本。</dd>
  </dl></MetricHelp>;
}
