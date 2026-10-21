"""Compile MediaProbe.java into android/classes.dex (only needed after editing the Java source).

A prebuilt classes.dex is committed. Rebuilding needs a JDK (17 recommended) and
the Android SDK with one platform and one build-tools version. Override
autodetection with JAVA_HOME, ANDROID_SDK_ROOT (or ANDROID_HOME),
ANDROID_PLATFORM (e.g. android-34) and ANDROID_BUILD_TOOLS (e.g. 35.0.0).
"""
import os
from pathlib import Path
import re
import shutil
import subprocess

root = Path(__file__).resolve().parent


def first_existing(paths):
    return next((Path(p) for p in paths if p and Path(p).exists()), None)


def newest(directory, pattern):
    names = [p.name for p in Path(directory).iterdir() if re.fullmatch(pattern, p.name)]
    if not names:
        raise SystemExit(f"Nothing matching {pattern} in {directory}")
    return max(names, key=lambda n: [int(x) for x in re.findall(r"\d+", n)])


sdk = first_existing([os.environ.get("ANDROID_SDK_ROOT"), os.environ.get("ANDROID_HOME"),
                      "/opt/homebrew/share/android-commandlinetools", Path.home() / "Library/Android/sdk",
                      "/usr/local/lib/android/sdk", Path.home() / "Android/Sdk"])
if not sdk:
    raise SystemExit("Android SDK not found: set ANDROID_SDK_ROOT")
platform = os.environ.get("ANDROID_PLATFORM") or newest(sdk / "platforms", r"android-\d+")
build_tools = os.environ.get("ANDROID_BUILD_TOOLS") or newest(sdk / "build-tools", r"\d+(?:\.\d+)*")
java_home = first_existing([os.environ.get("JAVA_HOME"),
                            "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home",
                            "/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home"])
if not java_home and shutil.which("javac"):
    java_home = Path(shutil.which("javac")).resolve().parent.parent
if not java_home:
    raise SystemExit("JDK not found: install a JDK (17 recommended) or set JAVA_HOME")
jar = sdk / "platforms" / platform / "android.jar"
out = root / "build"
out.mkdir(exist_ok=True)
env = {**os.environ, "JAVA_HOME": str(java_home)}
print(f"SDK {sdk} | {platform} | build-tools {build_tools} | JDK {java_home}")
subprocess.run([str(java_home / "bin/javac"), "--release", "8", "-Xlint:-options",
                "-classpath", str(jar), "-d", str(out), str(root / "MediaProbe.java")], env=env, check=True)
subprocess.run([str(sdk / "build-tools" / build_tools / "d8"), "--lib", str(jar), "--min-api", "23",
                "--output", str(out), str(out / "MediaProbe.class")], env=env, check=True)
shutil.copyfile(out / "classes.dex", root / "classes.dex")
print(root / "classes.dex")
