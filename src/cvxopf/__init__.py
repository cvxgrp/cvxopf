"""
cvxopf: AC optimal power flow via CVXPY DNLP and IPOPT.

Prerequisites
-------------
The IPOPT system library must be installed before installing this package,
and cyipopt must be installed separately:

  Ubuntu/Debian:
      sudo apt-get install coinor-libipopt-dev
      pip install cyipopt

  macOS:
      brew install ipopt
      pip install cyipopt

  Windows (conda recommended):
      conda install -c conda-forge ipopt
      pip install cyipopt

See https://coin-or.github.io/Ipopt/INSTALL.html for full details.
"""

_IPOPT_INSTALL_HINT = """
cyipopt is not importable. This usually means the IPOPT system library
was not present when cyipopt was built, or cyipopt was never installed.

Install the IPOPT system library first, then install cyipopt:

  Ubuntu/Debian:
      sudo apt-get install coinor-libipopt-dev
      pip install cyipopt

  macOS:
      brew install ipopt
      pip install cyipopt

  Windows (conda recommended):
      conda install -c conda-forge ipopt
      pip install cyipopt

Full instructions: https://coin-or.github.io/Ipopt/INSTALL.html
"""

try:
    import cyipopt  # noqa: F401
except ImportError as e:
    raise ImportError(_IPOPT_INSTALL_HINT) from e

__version__ = "0.1.0"

# Re-export public API
from cvxopf.problem import build_opf, build_opf_multistep, OPFOptions, OPFBuild
from cvxopf.results import extract_results, compare_to_reference
from cvxopf.storage import StorageUnitIdeal
from cvxopf.nondispatchable import NondispatchableUnit

__all__ = [
    "build_opf",
    "build_opf_multistep",
    "OPFOptions",
    "OPFBuild",
    "extract_results",
    "compare_to_reference",
    "StorageUnitIdeal",
    "NondispatchableUnit",
]
