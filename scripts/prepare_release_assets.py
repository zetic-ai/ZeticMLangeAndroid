#!/usr/bin/env python3
"""Create flat GitHub Release assets from an Android Maven publication bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


REQUIRED_ARTIFACTS = {
    "mlange",
    "core",
    "qnn_runtime",
    "qualcomm_runtime_v69",
    "qualcomm_runtime_v73",
    "qualcomm_runtime_v75",
    "qualcomm_runtime_v79",
    "qualcomm_runtime_v81",
    "ort_runtime",
    "executorch_runtime",
    "llama_cpp_runtime",
    "tflite_runtime",
    "litert_runtime",
    "litert_google_libraries",
    "litert_mediatek_libraries",
    "litert_qualcomm_libraries",
    "llm_runtime",
}
REQUIRED_SUFFIXES = (".aar", ".pom", ".module", "-sources.jar", "-javadoc.jar")


@dataclass(frozen=True)
class BundleEntry:
    name: str
    source_path: str
    group: str
    module: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def parse_entry(path: str, version: str) -> BundleEntry | None:
    parts = PurePosixPath(path).parts
    if len(parts) < 6 or parts[:3] != ("com", "zeticai", "mlange"):
        return None

    if parts[3] == "backend":
        if len(parts) != 7:
            return None
        group = "com.zeticai.mlange.backend"
        module, entry_version, name = parts[4:]
    else:
        if len(parts) != 6:
            return None
        group = "com.zeticai.mlange"
        module, entry_version, name = parts[3:]
    if entry_version != version or not name.endswith(REQUIRED_SUFFIXES):
        return None
    return BundleEntry(name=name, source_path=path, group=group, module=module)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sdk-repository", required=True)
    parser.add_argument("--sdk-checkout", type=Path, required=True)
    parser.add_argument("--build-infra-checkout", type=Path, required=True)
    parser.add_argument("--release-repository", required=True)
    return parser.parse_args()


def checked_git_value(checkout: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def read_property(path: Path, property_name: str) -> str:
    prefix = f"{property_name}="
    for line in path.read_text().splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix)
    raise ValueError(f"Missing {property_name} in {path}")


def source_provenance(args: argparse.Namespace) -> dict[str, str]:
    if checked_git_value(args.sdk_checkout, "status", "--porcelain"):
        raise ValueError(f"SDK checkout is not clean: {args.sdk_checkout}")
    if checked_git_value(args.build_infra_checkout, "status", "--porcelain"):
        raise ValueError(f"build_infra checkout is not clean: {args.build_infra_checkout}")
    sdk_build_infra_commit = (args.sdk_checkout / "build_infra-version.txt").read_text().strip()
    build_infra_commit = checked_git_value(args.build_infra_checkout, "rev-parse", "HEAD")
    if sdk_build_infra_commit != build_infra_commit:
        raise ValueError(
            "SDK build_infra pin does not match the supplied build_infra checkout: "
            f"{sdk_build_infra_commit} != {build_infra_commit}"
        )
    return {
        "sdk_commit": checked_git_value(args.sdk_checkout, "rev-parse", "HEAD"),
        "build_infra_commit": build_infra_commit,
        "native_version": read_property(
            args.sdk_checkout / "android" / "gradle.properties",
            "nexus.nativeVersion",
        ),
    }


def main() -> int:
    args = parse_arguments()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ValueError(f"Output directory is not empty: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    provenance = source_provenance(args)
    bundle_digest = sha256_file(args.bundle)
    entries: list[BundleEntry] = []
    module_metadata_entries: list[tuple[BundleEntry, dict]] = []
    with zipfile.ZipFile(args.bundle) as bundle:
        for source_path in bundle.namelist():
            entry = parse_entry(source_path, args.version)
            if entry is not None:
                entries.append(entry)

        if len({entry.name for entry in entries}) != len(entries):
            raise ValueError("Flat release asset names are not unique")
        modules = {entry.module for entry in entries}
        if modules != REQUIRED_ARTIFACTS:
            raise ValueError(
                "Unexpected module set: "
                f"expected {sorted(REQUIRED_ARTIFACTS)}, got {sorted(modules)}"
            )
        for module in REQUIRED_ARTIFACTS:
            names = {entry.name for entry in entries if entry.module == module}
            expected = {
                f"{module}-{args.version}{suffix}" for suffix in REQUIRED_SUFFIXES
            }
            if names != expected:
                raise ValueError(f"Incomplete release assets for {module}: {sorted(names)}")

        assets = []
        for entry in sorted(entries, key=lambda item: item.name):
            destination = args.output_dir / entry.name
            with bundle.open(entry.source_path) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
            assets.append(
                {
                    "name": entry.name,
                    "group": entry.group,
                    "module": entry.module,
                    "size_bytes": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                }
            )
            if entry.name.endswith(".module"):
                module_metadata = json.loads(destination.read_text())
                component = module_metadata.get("component", {})
                if component.get("group") != entry.group:
                    raise ValueError(f"Unexpected metadata group in {entry.name}")
                if component.get("module") != entry.module:
                    raise ValueError(f"Unexpected metadata module in {entry.name}")
                if component.get("version") != args.version:
                    raise ValueError(f"Unexpected metadata version in {entry.name}")
                module_metadata_entries.append((entry, module_metadata))

    asset_digests = {asset["name"]: asset["sha256"] for asset in assets}
    for entry, module_metadata in module_metadata_entries:
        for variant in module_metadata.get("variants", []):
            for file_metadata in variant.get("files", []):
                asset_name = file_metadata.get("url")
                expected_digest = file_metadata.get("sha256")
                if asset_name not in asset_digests:
                    raise ValueError(f"Missing metadata asset {asset_name} in {entry.name}")
                if asset_digests[asset_name] != expected_digest:
                    raise ValueError(f"Metadata digest mismatch for {asset_name} in {entry.name}")

    manifest = {
        "schema_version": 1,
        "version": args.version,
        "release_repository": args.release_repository,
        "source": {
            "sdk_repository": args.sdk_repository,
            **provenance,
            "publication_bundle": {
                "name": args.bundle.name,
                "size_bytes": args.bundle.stat().st_size,
                "sha256": bundle_digest,
            },
        },
        "assets": assets,
    }
    manifest_path = args.output_dir / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    checksum_paths = sorted(args.output_dir.iterdir(), key=lambda path: path.name)
    checksum_lines = [
        f"{sha256_file(path)}  {path.name}" for path in checksum_paths
    ]
    (args.output_dir / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n")
    print(f"Prepared {len(assets)} SDK assets in {args.output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        ValueError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
        json.JSONDecodeError,
    ) as error:
        print(f"prepare_release_assets: {error}", file=sys.stderr)
        raise SystemExit(1)
