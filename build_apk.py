#!/usr/bin/env python3
"""Build script for Thundr Swapper Android APK using local Android SDK and Java tools."""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
ANDROID_DIR = BASE_DIR / "android"
BUILD_DIR = BASE_DIR / "build" / "android"

SDK_ROOT = Path(os.getenv("ANDROID_HOME") or os.getenv("ANDROID_SDK_ROOT") or r"C:\Users\Tymur\AppData\Local\Android\Sdk")

# Pick highest available build-tools version
bt_parent = SDK_ROOT / "build-tools"
if bt_parent.exists():
    bt_versions = sorted([d for d in bt_parent.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
    BUILD_TOOLS_DIR = bt_versions[0] if bt_versions else (bt_parent / "34.0.0")
else:
    BUILD_TOOLS_DIR = SDK_ROOT / "build-tools" / "34.0.0"

# Find highest available android.jar
platforms_parent = SDK_ROOT / "platforms"
if platforms_parent.exists():
    plat_versions = sorted([d for d in platforms_parent.iterdir() if d.is_dir() and (d / "android.jar").exists()], key=lambda d: d.name, reverse=True)
    PLATFORM_JAR = (plat_versions[0] / "android.jar") if plat_versions else (platforms_parent / "android-34" / "android.jar")
else:
    PLATFORM_JAR = SDK_ROOT / "platforms" / "android-34" / "android.jar"

is_win = os.name == "nt"

def find_sdk_tool(tool_name: str) -> Path:
    if is_win:
        for ext in (".exe", ".bat", ".cmd", ""):
            cand = BUILD_TOOLS_DIR / f"{tool_name}{ext}"
            if cand.is_file():
                return cand
    else:
        cand = BUILD_TOOLS_DIR / tool_name
        if cand.is_file():
            return cand
    which_path = shutil.which(tool_name)
    if which_path:
        return Path(which_path)
    return BUILD_TOOLS_DIR / (f"{tool_name}.exe" if is_win else tool_name)

AAPT2 = find_sdk_tool("aapt2")
D8 = find_sdk_tool("d8")
ZIPALIGN = find_sdk_tool("zipalign")
APKSIGNER = find_sdk_tool("apksigner")


def run_cmd(cmd, desc):
    print(f"[*] {desc}...")
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR))
    if res.returncode != 0:
        print(f"[!] Error during {desc}:")
        print(res.stdout)
        print(res.stderr)
        sys.exit(1)
    return res


def build():
    if not SDK_ROOT.exists():
        print(f"❌ Android SDK not found at {SDK_ROOT}")
        sys.exit(1)

    print(f"[*] Using Android SDK: {SDK_ROOT}")
    print(f"[*] Using build-tools: {BUILD_TOOLS_DIR}")

    # Clean build directory
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    gen_dir = BUILD_DIR / "gen"
    obj_dir = BUILD_DIR / "obj"
    gen_dir.mkdir(parents=True, exist_ok=True)
    obj_dir.mkdir(parents=True, exist_ok=True)

    # 1. Update bundled accounts in assets
    print("[*] Updating assets/accounts.json...")
    from thundr.account import AccountManager
    from thundr.launcher import export_accounts_for_mobile
    import json

    mgr = AccountManager()
    accs = mgr.list_accounts(enabled_only=False)
    payload = export_accounts_for_mobile(accs)
    assets_dir = ANDROID_DIR / "src" / "main" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "accounts.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # 2. AAPT2 compile resources
    res_zip = BUILD_DIR / "res.zip"
    run_cmd([
        str(AAPT2), "compile",
        "--dir", str(ANDROID_DIR / "src" / "main" / "res"),
        "-o", str(res_zip),
    ], "Compiling resources with aapt2")

    # 3. AAPT2 link resources
    unaligned_apk = BUILD_DIR / "unaligned.apk"
    run_cmd([
        str(AAPT2), "link",
        "-I", str(PLATFORM_JAR),
        "--manifest", str(ANDROID_DIR / "src" / "main" / "AndroidManifest.xml"),
        "-o", str(unaligned_apk),
        "-A", str(assets_dir),
        str(res_zip),
        "--java", str(gen_dir),
        "--auto-add-overlay",
    ], "Linking APK package with aapt2")

    # 4. Compile Java sources with javac
    java_files = list((ANDROID_DIR / "src" / "main" / "java" / "com" / "thundr" / "swapper").glob("*.java"))
    r_java = list(gen_dir.glob("**/R.java"))
    all_java = [str(f) for f in (java_files + r_java)]

    run_cmd([
        "javac",
        "-source", "1.8",
        "-target", "1.8",
        "-encoding", "UTF-8",
        "-cp", str(PLATFORM_JAR),
        "-d", str(obj_dir),
    ] + all_java, "Compiling Java sources with javac")

    # 5. Compile bytecode to DEX using d8
    class_files = [str(f) for f in obj_dir.glob("**/*.class")]
    run_cmd([
        str(D8),
        "--release",
        "--min-api", "24",
        "--output", str(BUILD_DIR),
    ] + class_files, "Generating classes.dex with d8")

    # 6. Add classes.dex to unaligned.apk
    dex_file = BUILD_DIR / "classes.dex"
    print("[*] Adding classes.dex to APK...")
    with zipfile.ZipFile(unaligned_apk, "a") as z:
        z.write(dex_file, "classes.dex")

    # 7. Zipalign APK
    aligned_apk = BUILD_DIR / "aligned.apk"
    run_cmd([
        str(ZIPALIGN),
        "-p", "-f", "4",
        str(unaligned_apk),
        str(aligned_apk),
    ], "Aligning APK with zipalign")

    # 8. Create debug keystore if not exists
    keystore = BASE_DIR / "debug.keystore"
    if not keystore.exists():
        print("[*] Generating debug keystore...")
        subprocess.run([
            "keytool", "-genkeypair",
            "-keystore", str(keystore),
            "-alias", "androiddebugkey",
            "-storepass", "android",
            "-keypass", "android",
            "-keyalg", "RSA",
            "-keysize", "2048",
            "-validity", "10000",
            "-dname", "CN=Android Debug,O=Android,C=US",
        ], capture_output=True, check=True)

    # 9. Sign APK with apksigner (v1 + v2 schemes for maximum Android compatibility)
    final_apk = BASE_DIR / "thundr-swapper.apk"
    run_cmd([
        str(APKSIGNER), "sign",
        "--ks", str(keystore),
        "--ks-pass", "pass:android",
        "--key-pass", "pass:android",
        "--ks-key-alias", "androiddebugkey",
        "--v1-signing-enabled", "true",
        "--v2-signing-enabled", "true",
        "--out", str(final_apk),
        str(aligned_apk),
    ], "Signing APK with apksigner")

    size_mb = final_apk.stat().st_size / (1024 * 1024)
    print("\n" + "=" * 60)
    print("✅ ANDROID APK SUCCESSFULLY BUILT!")
    print(f"📦 APK Location: {final_apk}")
    print(f"📊 Size: {size_mb:.2f} MB")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    build()
