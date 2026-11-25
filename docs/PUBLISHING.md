# Publish Warp-Manager / Загрузка на GitHub

Target repository: **Forcus356/Warp-Manager**, default branch **main**. The archive is prepared for this name; creating/uploading the repository is a maintainer action. The preparation process does not publish it automatically.

## Upload the full project

1. Extract `Warp-Manager-v0.4.1.zip`.
2. Create an empty `Warp-Manager` repository in the Forcus356 account.
3. Upload the **contents** of the extracted `Warp-Manager` directory to the repository root, including `.github`, `.gitignore` and `.gitattributes`. Do not add another wrapper directory around README.md.
4. Commit to `main`. The CI workflows become available after this upload.

From a local checkout, an equivalent Git workflow is:

```bash
git init -b main
git add .
git commit -m "Initial Warp-Manager source"
git remote add origin https://github.com/Forcus356/Warp-Manager.git
git push -u origin main
```

Create the empty repository first; use your normal GitHub authentication. No access token belongs in these files. The remote bootstrap link in README starts working only when its script and the complete source tree are public in that branch.

## Validate before a release

```bash
python3 scripts/check_project.py
python3 -m unittest discover -s tests -v
python3 scripts/build_release.py --expected-tag v0.4.1
```

The builder writes `dist/Warp-Manager-v0.4.1.zip` and `dist/SHA256SUMS`. The source ZIP includes all READMEs, docs, examples, tests, workflows and an internal file manifest. `dist/`, private runtime files and caches are excluded. A committed old MANIFEST describes its original archive; each new build regenerates the archive manifest from the current source.

## Tags, Actions and GitHub Releases

After reviewing the source and checks:

```bash
git tag v0.4.1
git push origin v0.4.1
```

The Release artifact workflow validates the tag against VERSION, runs the suite and uploads ZIP/checksum as an **Actions artifact**. It does not automatically publish a GitHub Release or write to the repository. Download the files from that artifact or build them locally, create a GitHub Release for the tag and attach the ZIP and SHA256SUMS yourself.

An Actions artifact download requires GitHub authentication and is not the same as a public Release asset URL. Once the release and assets are published, the public ZIP follows this form:

```text
https://github.com/Forcus356/Warp-Manager/releases/download/v0.4.1/Warp-Manager-v0.4.1.zip
```

Do not advertise that asset URL before attaching the asset. The standalone bootstrap can already use `--source-ref v0.4.1` from the tag without a Release asset.

## Future versions

Update both VERSION and warp_pool.VERSION, add CHANGELOG notes, refresh examples/previews when their underlying code changes and rerun checks. Keep English/Russian reference and translated READMEs consistent about units, policies and verified limits. Preserve notices for adapted code and separately downloaded components.

Preview regeneration is optional and requires Pillow plus DejaVu Sans Mono (`python3-pil` and `fonts-dejavu-core` on Debian/Ubuntu). The runtime, automated tests and archive builder use only the Python standard library.

```bash
python3 scripts/render_previews.py
python3 scripts/check_project.py
python3 scripts/build_release.py
```

For a fork, change the default repository in `install_warp_manager.sh` and public links, or pass `--source-repo OWNER/REPO` explicitly. Internal installed names remain compatible and need not be renamed.
