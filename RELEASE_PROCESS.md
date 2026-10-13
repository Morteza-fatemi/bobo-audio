# Source release procedure

**Scope:** source-code previews, not certified installers or production deployments.

## After a reviewed change

1. Merge a reviewed Pull Request into `main` only after the normal CI succeeds.
2. Ensure the intended release commit is reachable from `main`.
3. Create a SemVer tag on that commit (example version below; never reuse an existing tag):

```bash
git checkout main
git pull --ff-only
git tag -a v0.2.0 -m "Source preview v0.2.0"
git push origin v0.2.0
```

4. The tag triggers `Versioned Source Release`. It reruns platform/application tests on the **tagged commit** and publishes a **prerelease** with generated notes **only if every gate succeeds**. Invalid SemVer tags or tags not reachable from main are rejected.
5. Review the public release page, migration/security notes and linked CI. The workflow creates no binary packages and does not claim a production acceptance test.

## Historical source preview

Version `v0.1.0` was published before this versioned workflow existed and its GitHub release metadata is not automatically changed by this update. The release notes describe it as a source preview; a maintainer with release permissions may explicitly mark it as a prerelease with:

```bash
gh release edit v0.1.0 --prerelease
```

Run that command only if you agree that the existing preview should be designated a prerelease. GitHub repository administration is not available through the current read/write source connector.

## Verification limit

CI cannot prove Windows hardware and microphone support, third-party music catalog availability, hosted production security or end-to-end customer workflows. These require independent testing and explicitly documented conditions.
