# testfabric.spec — YAML spec schema, validation & parametrization
#
# Micro-package: testfabric[yaml]
# Dependencies: pydantic, pyyaml
#
# Provides:
#   - RunSpec: full YAML-driven run specification (load, validate, expand)
#   - Parametrization: variable expansion ($VAR, ${VAR}), profile overlays
#   - Lint: static analysis of raw spec YAML
#   - Schema sections: PipelineSection, StageSection, SuiteSection, DockerSection, …
#
# Usage:
#   from testfabric.spec import RunSpec
#   spec = RunSpec.load("run.yaml", profile="ci")
#
#   from testfabric.spec import lint_raw_spec
#   for issue in lint_raw_spec(raw_dict):
#       print(issue)

from .schema import RunSpec
from .parametrize import ParametrizationError, parse_assignment_list
from .lint import LintIssue, lint_raw_spec

__all__ = [
    "LintIssue",
    "ParametrizationError",
    "RunSpec",
    "lint_raw_spec",
    "parse_assignment_list",
]
