# Contributing

Thanks for helping improve Creator Video Data.

Useful contributions include verified platform routes, clearer input examples, regression tests, and fixes to the local tools.

Before submitting a change:

1. Run `python3 -m unittest discover -s tests -v`.
2. Use synthetic fixtures. Keep credentials, cookies, personal paths, and private customer material out of the repository.
3. Describe the observed problem, the resulting behavior, and the checks you ran.
4. Identify platform, quota, or login assumptions when proposing a collection route.

The scripts are offline tools. A successful local test does not verify live platform access or cloud persistence.

Open an issue for larger changes so the intended scope is clear. Contributions are provided under the project's MIT License.
