"""Stable per-task, per-job output locations shared by execution and archives."""

def output_directory(task_id, job_id):
    return '/var/tmp/hive/outputs/' + task_id + '/' + job_id
