"""
Built-in MATPOWER-format test cases.

Available test cases
--------------------
case9 : 9-bus, 3-generator system (Chow, p. 70)
case14 : IEEE 14-bus test case

Usage
-----
    from cvxopf.testcases import case9, case14

    ppc = case9()
    ppc = case14()
"""

from cvxopf.testcases.case9 import case9
from cvxopf.testcases.case14 import case14

__all__ = ["case9", "case14"]
