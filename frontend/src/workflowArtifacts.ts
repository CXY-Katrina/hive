import type { WorkflowArtifactSpec, WorkflowEnvironment, WorkflowJob } from './workflowTypes';
export function environmentTargets(environments: WorkflowEnvironment[], alias?: string) {
  return environments.filter(env => alias == null || env.alias === alias).flatMap(env => (env.node_aliases || [env.node_alias]).filter(Boolean).map(node_alias => ({environment: env.alias, node_alias})));
}
export function artifactTargets(artifact: WorkflowArtifactSpec, job: WorkflowJob, environments: WorkflowEnvironment[]) {
  const valid = environmentTargets(environments);
  return artifact.targets === undefined ? environmentTargets(environments, artifact.environment || job.environment) : artifact.targets.filter(target => valid.some(item => item.environment === target.environment && item.node_alias === target.node_alias));
}
