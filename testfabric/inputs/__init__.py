# testfabric.inputs — Input resolution, profiles & validation
#
# Micro-package: testfabric[yaml]
# Dependencies: pydantic
#
# Provides:
#   - InputDefinition: typed input declarations (string, secret, int, bool, …)
#   - InputResolver: resolve inputs from CLI, env, profiles, defaults
#   - ProfileDefinition: named input presets (ci, staging, prod, …)
#   - ResolvedInputs: final resolved inputs with expansion map
#
# Usage:
#   from testfabric.inputs import InputResolver
#   resolver = InputResolver()
#   resolved = resolver.resolve(input_defs=defs, profile=selected, cli_inputs=overrides)

from testfabric.inputs.models import InputDefinition, InputType, ProfileDefinition, ResolvedInputs
from testfabric.inputs.resolver import InputResolutionError, InputResolver

__all__ = [
    "InputDefinition",
    "InputResolutionError",
    "InputResolver",
    "InputType",
    "ProfileDefinition",
    "ResolvedInputs",
]
