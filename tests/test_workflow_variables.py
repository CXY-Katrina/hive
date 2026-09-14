"""Public workflow variable names must identify one job unambiguously."""
import unittest
from pydantic import ValidationError
from hive.workflow_schemas import WorkflowCreate
from tests.test_workflows import payload


class WorkflowVariableNames(unittest.TestCase):
    def test_ambiguous_normalized_job_names_are_rejected(self):
        spec = payload()
        spec['jobs'][0]['id'] = 'serve-one'
        spec['jobs'][1]['id'] = 'serve_one'
        for job in spec['jobs']:
            job['depends_on'] = []
        with self.assertRaisesRegex(ValidationError, 'environment variable'):
            WorkflowCreate.model_validate(spec)
