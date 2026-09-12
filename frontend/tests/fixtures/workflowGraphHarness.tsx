import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { WorkflowGraph } from '../../src/components/WorkflowGraph';
import type { WorkflowJob } from '../../src/workflowTypes';
import '../../src/styles.css';

const initial = (window as unknown as { graphJobs: WorkflowJob[] }).graphJobs;
function Harness() {
  const [jobs, setJobs] = useState(initial);
  return <main style={{ padding: 24 }}><WorkflowGraph jobs={jobs} onChange={setJobs} /><output data-testid="graph-value" style={{ display: 'none' }}>{JSON.stringify(jobs)}</output></main>;
}
createRoot(document.getElementById('root')!).render(<Harness />);
