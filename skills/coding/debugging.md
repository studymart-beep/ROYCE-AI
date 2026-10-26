---
name: debugging
description: Root-cause method for bugs: reproduce, isolate, minimal fix, verify. Use when something is broken or a test fails.
---
# Debugging

Goal: find the cause, not the symptom. Fix the smallest thing that is actually wrong.

1. Reproduce. Get the exact failing command, input, or test. If you cannot reproduce it, say so and list what you tried. Do not patch an unreproduced bug.
2. Read the evidence. Full traceback or log line, expected vs observed output. Check the exact value that was wrong, not the feeling that something is off.
3. Isolate. Narrow the path: binary search with prints or a small script, run one function alone, check inputs at each hop. Stop at the first hop where the value is already wrong.
4. Form one hypothesis and test it. Change one thing, rerun. If the hypothesis is wrong, discard it, do not stack fixes.
5. Decide whether the bug is in the code or the test. If the fixture is wrong (bad data, wrong format), fix the fixture. If the code violates the intended behavior, fix the code. Say which and why.
6. Minimal fix. Change the fewest lines that address the cause. No drive-by refactors, no unrelated cleanups.
7. Verify. Rerun the reproducing case, then the full test suite. Report the real pass/fail counts.
8. Report. Cause in one sentence, the fix (file and function), the verification result, and any risk or follow-up.

Anti-patterns: wrapping code in try/except to hide errors; asserting `or True`; changing expected values to match wrong output; claiming a fix without rerunning.
