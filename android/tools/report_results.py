"""Print actual JUnit results for review-build evidence; never mark missing tests passed."""
from pathlib import Path
import xml.etree.ElementTree as ET

paths = sorted(Path("app/build").glob("**/TEST-*.xml"))
total = failed = skipped = 0
for path in paths:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in suites:
        tests = int(suite.get("tests", 0))
        failures = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
        skips = int(suite.get("skipped", 0))
        print(f"{suite.get('name')}: tests={tests}, failures/errors={failures}, skipped={skips}")
        total += tests
        failed += failures
        skipped += skips
print(f"TOTAL: tests={total}, failures/errors={failed}, skipped={skipped}")
if not paths:
    print("No JUnit XML reports available. Tests are NOT verified.")
