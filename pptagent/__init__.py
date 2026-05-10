"""PPTAgent: Generating and Evaluating Presentations Beyond Text-to-Slides.

This package provides tools to automatically generate presentations from documents,
following a two-phase approach of Analysis and Generation.

For more information, visit: https://github.com/icip-cas/PPTAgent
"""

__version__ = "0.1.0"
__author__ = "Hao Zheng"
__email__ = "wszh712811@gmail.com"


# Check the version of python and python-pptx
import sys
from importlib import import_module

if sys.version_info < (3, 11):
    raise ImportError("You should use Python 3.11 or higher for this project.")

_optional_dependency_error: Exception | None = None
try:
    from packaging.version import Version
    from pptx import __version__ as PPTXVersion

    try:
        PPTXVersion, Mark = PPTXVersion.split("+")
        assert Version(PPTXVersion) >= Version("1.0.4") and Mark == "PPTAgent"
    except Exception as exc:
        raise ImportError(
            "You should install the customized `python-pptx` for this project: Force1ess/python-pptx, but got %s."
            % PPTXVersion
        ) from exc
except Exception as exc:
    _optional_dependency_error = exc


def _safe_star_import(module_name: str) -> None:
    try:
        module = import_module(f".{module_name}", __name__)
    except Exception:
        return
    exported = getattr(module, "__all__", None)
    if exported is None:
        return
    globals().update({name: getattr(module, name) for name in exported})


# Import main modules to make them directly accessible when importing the package
for _module_name in [
    "agent",
    "apis",
    "document",
    "induct",
    "llms",
    "model_utils",
    "multimodal",
    "pptgen",
    "presentation",
    "research",
    "utils",
]:
    _safe_star_import(_module_name)

# Define the top-level exports
__all__ = [
    "agent",
    "pptgen",
    "document",
    "llms",
    "presentation",
    "utils",
    "apis",
    "model_utils",
    "multimodal",
    "induct",
    "research",
]
