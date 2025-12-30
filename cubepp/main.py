#!/usr/bin/env python3
"""
STM32 CMake Project Setup Script

このスクリプトは、CMakeプロジェクトファイルを自動的に修正します。
変更内容はCONFIGURATION部分で一元管理されています。
"""

import json
import re
import shutil
from pathlib import Path
import argparse


# ============================================================================
# CONFIGURATION - 変更内容をここで定義
# ============================================================================

CONFIG = {
    # CMakePresets.json の設定
    "cmake_presets": {
        "binary_dir": "${sourceDir}/.build/${presetName}",  # build -> .build に変更
    },
    # CMakeLists.txt の設定 - set() で設定する変数
    "cmake_variables": {
        "CMAKE_CXX_STANDARD": "20",
        "CMAKE_CXX_STANDARD_REQUIRED": "ON",
        "CMAKE_CXX_EXTENSIONS": "ON",
    },
    # CMakeLists.txt の設定 - target_XXX() 関数
    "cmake_functions": {
        "target_sources": ["${SOURCES}"],
        "target_include_directories": [
            "${PROJECT_SOURCE_DIR}/app/include",
        ],
        "target_compile_definitions": [
            '"__weak=__attribute__((weak))"',
            '"__packed=__attribute__((__packed__))"',
        ],
        "target_compile_options": ["-Wall", "-Wextra", "-fdiagnostics-color=always"],
        "target_link_libraries": [
            "nosys",
        ],
        "target_link_options": [
            "--specs=nosys.specs",
            "-Wl,-u,_printf_float,-u,scanf_float",
        ],
    },
    "extra": """
file(GLOB_RECURSE STM32_SOURCES
    Drivers/**
)
foreach(f ${STM32_SOURCES})
    set_source_files_properties(${f} PROPERTIES COMPILE_OPTIONS "-w")
endforeach()
            """,
    "profiles": {
        "armmath": {
            "cmake_functions": {
                "target_compile_definitions": [
                    "ARM_MATH_CM4",
                ],
                "target_link_libraries": [
                    "arm_cortexM4lf_math",
                ],
            },
        },
        "dsp": {
            "cmake_functions": {
                "target_link_directories": [
                    "${PROJECT_SOURCE_DIR}/Drivers/CMSIS/DSP/Lib/GCC"
                ],
                "target_include_directories": [
                    "${PROJECT_SOURCE_DIR}/Drivers/CMSIS/DSP/Include"
                ],
            },
        },
        "printf": {
            "source_file_injections": [
                {
                    "file": "Core/Src/main.c",
                    "marker": "/* USER CODE BEGIN 1 */",
                    "content": """  setvbuf(stdin, NULL, _IONBF, 0);
  setvbuf(stdout, NULL, _IONBF, 0);""",
                    "check": "setvbuf",
                },
                {
                    "file": "Core/Src/main.c",
                    "marker": "/* USER CODE BEGIN 0 */",
                    "content": """int _write(int file, char *ptr, int len)
{
  (void) file;
  HAL_UART_Transmit(&huart, (uint8_t *)ptr, len, HAL_MAX_DELAY);
  return len;
}

int _read(int file, char *ptr, int len)
{
  (void) file;
  uint32_t rx_counter = 0;
  while (rx_counter < len) {
    int status = HAL_UART_Receive(&huart, (uint8_t *)&ptr[rx_counter], 1, HAL_MAX_DELAY);
    if (status != HAL_OK) {
      break;
    } else if (ptr[rx_counter] == '\\n' || ptr[rx_counter] == '\\r') {
      ptr[rx_counter] = '\\0';
    }
    ++rx_counter;
  }
  return rx_counter;
}""",
                    "check": "_write",
                },
            ],
        },
    },
    # 特殊処理が必要な設定
    "cmake_special": {
        # アプリケーションソースファイルのパターン（file(GLOB_RECURSE)用）
        "source_patterns": ["${PROJECT_SOURCE_DIR}/app/src/**.c**"],
    },
    # resources ディレクトリのコピー設定
    "resources": {
        # 探索するリソースディレクトリ（順に探索し、存在したものをコピー）
        # 相対パスは setup.py の所在または実行ディレクトリ（カレントディレクトリ）基準で解決
        "paths": ["resources"],
    },
    # ソースファイルへの自動挿入設定
    "source_file_injections": [
        {
            "file": "Core/Src/main.c",
            "marker": "/* USER CODE BEGIN Includes */",
            "content": '#include "{project_name}/main_exec.h"',
            "check": "main_exec.h",  # 既存チェック用の文字列
        },
        {
            "file": "Core/Src/main.c",
            "marker": "/* USER CODE BEGIN 2 */",
            "content": "  Setup();",
            "check": "Setup();",
        },
        {
            "file": "Core/Src/main.c",
            "marker": "/* USER CODE BEGIN 3 */",
            "content": "  Loop();",
            "check": "Loop();",
        },
    ],
}


# ============================================================================
# IMPLEMENTATION - 実装部分
# ============================================================================


class ProjectSetup:
    """プロジェクト設定を管理するクラス"""

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir

    def update_cmake_presets(self, config: dict[str, any]):
        """CMakePresets.jsonを更新"""
        presets_file = self.root_dir / "CMakePresets.json"

        with open(presets_file, "r") as f:
            data = json.load(f)

        # binaryDirを更新
        for preset in data.get("configurePresets", []):
            if preset.get("name") == "default":
                preset["binaryDir"] = config["cmake_presets"]["binary_dir"]

        with open(presets_file, "w") as f:
            json.dump(data, f, indent=4)

        print(f"✓ Updated {presets_file.name}")

    def update_cmake_lists(self, config: dict[str, any]):
        """CMakeLists.txtを更新"""
        cmake_file = self.root_dir / "CMakeLists.txt"

        with open(cmake_file, "r") as f:
            content = f.read()

        # 1. set() 変数を更新
        content = self._update_cmake_variables(content, config)

        # 2. ソースファイルのGLOB処理を追加（特殊処理）
        content = self._update_source_glob(content, config)

        # 3. target_XXX() 関数を更新
        content = self._update_cmake_functions(content, config)

        with open(cmake_file, "w") as f:
            f.write(content)

        print(f"✓ Updated {cmake_file.name}")

    def _update_cmake_variables(self, content: str, config: dict[str, any]) -> str:
        """set() で設定する変数を更新または追加"""
        variables = config.get("cmake_variables", {})

        for var_name, var_value in variables.items():
            pattern = rf"set\({re.escape(var_name)}\s+[^\)]+\)"
            replacement = f"set({var_name} {var_value})"

            if re.search(pattern, content):
                # 既存の変数を更新
                content = re.sub(pattern, replacement, content)
            else:
                # 新規追加: "# Setup compiler settings" セクションの set() の最後に追加
                marker = r"(# Setup compiler settings.*?)((?:set\([^\)]+\)\n)+)"
                match = re.search(marker, content, re.DOTALL)
                if match:
                    insert_pos = match.end(2)
                    content = (
                        content[:insert_pos] + f"{replacement}\n" + content[insert_pos:]
                    )
                else:
                    # マーカーが見つからない場合は先頭に追加
                    content = f"{replacement}\n\n" + content

        return content

    def _update_source_glob(self, content: str, config: dict[str, any]) -> str:
        """file(GLOB_RECURSE) によるソースファイル追加を処理"""
        special = config.get("cmake_special", {})
        patterns = special.get("source_patterns", [])

        if not patterns:
            return content

        glob_patterns = "\n  ".join(patterns)
        sources_section = f"""# Add sources to executable
file(GLOB_RECURSE SOURCES
  {glob_patterns}
)
target_sources(${{CMAKE_PROJECT_NAME}} PRIVATE
    # Add user sources here
    ${{SOURCES}}
)"""

        # 既存の target_sources を置換
        pattern = r"# Add sources to executable\ntarget_sources\(\$\{CMAKE_PROJECT_NAME\}\s+PRIVATE\s*\n\s*# Add user sources here\s*\n\)"
        if re.search(pattern, content):
            content = re.sub(pattern, sources_section, content)

        return content

    def _update_cmake_functions(self, content: str, config: dict[str, any]) -> str:
        """target_XXX() 関数の引数を更新または関数を追加"""
        target_funcs = config.get("cmake_functions", {})

        for func_name, items in target_funcs.items():
            if not items:
                continue

            # target_sources は特殊処理済みなのでスキップ
            if func_name == "target_sources":
                continue

            # 関数のパターンを探す
            # 例: target_link_directories(${CMAKE_PROJECT_NAME} PRIVATE\n    \n)
            pattern = (
                rf"({re.escape(func_name)}\(\$\{{CMAKE_PROJECT_NAME\}}[\s\S]*?)(\n\))"
            )
            match = re.search(pattern, content, re.DOTALL)

            if match:
                items_to_add = []
                for item in items:
                    # 既に存在しない項目のみ追加
                    if not item in match.group(0):
                        items_to_add.append(item)
                if not items_to_add:
                    continue
                # 既存の関数に項目を追加
                items_str = "\n    ".join(items_to_add)
                # コメント行の後に項目を挿入
                new_func = f"{match.group(1)}\n    {items_str}{match.group(2)}"
                content = content[: match.start()] + new_func + content[match.end() :]
            else:
                # 関数が存在しない場合は末尾に追加
                items_str = "\n    ".join(items)

                # 適切な修飾子を決定
                modifier = "PRIVATE"
                if func_name == "target_compile_options":
                    modifier = "PUBLIC"

                new_func = f"""\n{func_name}(${{CMAKE_PROJECT_NAME}} {modifier}
    {items_str}
)"""
                content = content.rstrip() + "\n" + new_func + "\n"

        return content

    def update_cmake_extra(self, config: dict[str, any]):
        """CMakeLists.txt に extra セクションを追加"""
        cmake_file = self.root_dir / "CMakeLists.txt"

        with open(cmake_file, "r") as f:
            content = f.read()

        extra = config.get("extra", "").strip()
        if extra:
            # 既存の extra セクションを削除
            pattern = r"# Extra CMake configurations[\s\S]*?(?=\n#|$)"
            content = re.sub(pattern, "", content, flags=re.DOTALL).rstrip()

            # 末尾に追加
            content += f"\n\n# Extra CMake configurations\n{extra}\n"

        with open(cmake_file, "w") as f:
            f.write(content)

        print(f"✓ Updated extra section in {cmake_file.name}")

    def copy_resources(self, config: dict[str, any]) -> set:
        """resources 配下のファイルを実行ディレクトリへ展開し、コピーされたファイルのセットを返す"""
        cfg = config.get("resources", {})
        paths: list[str] = cfg.get("paths", [])
        copied_files = set()

        if not paths:
            print("! No resource paths configured; skipped copying.")
            return copied_files

        dest_root = Path.cwd()
        script_dir = Path(__file__).resolve().parent
        copied_any = False

        for raw_path in paths:
            candidates = []
            p = Path(raw_path)
            if p.is_absolute():
                candidates.append(p)
            else:
                candidates.append(script_dir / p)
                candidates.append(dest_root / p)

            src_dir = next((c for c in candidates if c.exists() and c.is_dir()), None)
            if src_dir is None:
                continue

            files = self._copy_tree(src_dir, dest_root)
            copied_files.update(files)
            copied_any = True
            print(f"✓ Copied resources from {src_dir} to {dest_root}")

        if not copied_any:
            print("! No resource directories found; skipped copying.")

        return copied_files

    def post_process_projectname(self, dest_root: Path, copied_files: set, config: dict[str, any]):
        """コピー後の置換処理:
        - `projectname` というディレクトリ名をルート名に置換（コピーされたファイルのみ）
        - ソース内の `{{PROJECTNAME}}` をルート名に置換（コピーされたファイルのみ）
        - ソース内の `{{STM32TYPE}}` を cmake/stm32cubemx/CMakeLists.txt から抽出したSTM32型名に置換（コピーされたファイルのみ）
        - ソースファイルへの自動挿入処理
        """
        project_name = dest_root.name
        stm32_type = self._extract_stm32_type()
        renamed_files = set()

        # 1) ディレクトリ名 'projectname' をプロジェクト名に置換（コピーされたディレクトリのみ）
        # コピーされたファイルから親ディレクトリを抽出
        copied_dirs = set()
        for f in copied_files:
            for parent in f.parents:
                if parent == dest_root:
                    break
                copied_dirs.add(parent)

        for d in sorted(copied_dirs, key=lambda x: len(x.parts), reverse=True):
            if d.is_dir() and d.name == "projectname":
                target = d.parent / project_name
                if target.exists():
                    # 既に存在する場合はマージ—ファイルをコピーして上書き
                    for srcfile in d.rglob("*"):
                        if srcfile.is_file():
                            rel = srcfile.relative_to(d)
                            destfile = target / rel
                            destfile.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(srcfile, destfile)
                            # リネーム後のファイルパスを記録
                            if srcfile in copied_files:
                                copied_files.discard(srcfile)
                                renamed_files.add(destfile)
                    # 元の空ディレクトリは削除
                    try:
                        for sub in sorted(d.rglob("*"), reverse=True):
                            if sub.is_file():
                                sub.unlink()
                        d.rmdir()
                    except Exception:
                        pass
                else:
                    # ディレクトリをリネーム
                    # リネームされたファイルパスを更新
                    for srcfile in list(copied_files):
                        try:
                            rel = srcfile.relative_to(d)
                            new_file = target / rel
                            copied_files.discard(srcfile)
                            renamed_files.add(new_file)
                        except ValueError:
                            # このファイルは d の配下ではない
                            pass
                    d.rename(target)
                print(f"✓ Renamed directory {d} -> {target}")

        # リネームされたファイルをコピーリストに追加
        copied_files.update(renamed_files)

        # 2) ファイル内のプレースホルダ置換（コピーされたファイルのみ）
        placeholders = {"{{PROJECTNAME}}": project_name, "{{STM32TYPE}}": stm32_type}

        for f in copied_files:
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                # バイナリや読めないファイルはスキップ
                continue

            modified = False
            for placeholder, replacement in placeholders.items():
                if placeholder in text:
                    text = text.replace(placeholder, replacement)
                    modified = True

            if modified:
                f.write_text(text, encoding="utf-8")
                print(f"✓ Replaced placeholders in {f}")

        # 3) ソースファイルへの自動挿入処理
        self._inject_to_source_files(dest_root, project_name, config=config)

    def _inject_to_source_files(self, dest_root: Path, project_name: str, config: dict[str, any]):
        """CONFIGで定義されたソースファイルに内容を挿入"""
        # CONFIGから挿入設定を取得
        injections = config.get("source_file_injections", [])
        
        if not injections:
            return

        # ファイルごとにグループ化
        files_to_inject = {}
        for injection in injections:
            file_path = injection.get("file")
            if not file_path:
                continue
            
            if file_path not in files_to_inject:
                files_to_inject[file_path] = []
            files_to_inject[file_path].append(injection)
        
        # 各ファイルに対して処理
        for file_path, file_injections in files_to_inject.items():
            target_file = dest_root / file_path
            if not target_file.exists():
                print(f"! {target_file} not found; skipped insertion")
                continue

            try:
                src = target_file.read_text(encoding="utf-8")
            except Exception:
                src = ""

            new_src = src
            changed = False

            for injection in file_injections:
                marker = injection.get("marker")
                content_template = injection.get("content")
                check = injection.get("check")

                if not marker or not content_template:
                    continue

                # プロジェクト名の置換が必要な場合のみ format() を使用
                if "{project_name}" in content_template:
                    content = content_template.format(project_name=project_name)
                else:
                    content = content_template

                # マーカーが存在し、チェック文字列が含まれていない場合に挿入
                if marker in new_src and (not check or check not in new_src):
                    parts = new_src.split(marker, 1)
                    new_src = parts[0] + marker + "\n" + content + "\n" + parts[1]
                    changed = True

            if changed:
                target_file.write_text(new_src, encoding="utf-8")
                print(f"✓ Modified {target_file}")

    def _copy_tree(self, src: Path, dest_root: Path) -> set:
        """src 配下のファイルを dest_root へ相対パスを保ってコピーし、コピーされたファイルのセットを返す
        既存のファイルがある場合は上書きしない"""
        copied_files = set()
        for path in src.rglob("*"):
            if path.is_dir():
                continue

            rel = path.relative_to(src)
            dest_path = dest_root / rel

            # 既存ファイルがある場合はスキップ
            if dest_path.exists():
                print(f"  Skipped (already exists): {dest_path}")
                continue

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest_path)
            copied_files.add(dest_path)
        return copied_files

    def _extract_stm32_type(self) -> str:
        """cmake/stm32cubemx/CMakeLists.txt から STM32 型名を抽出"""
        cubemx_cmake = self.root_dir / "cmake" / "stm32cubemx" / "CMakeLists.txt"

        if not cubemx_cmake.exists():
            print(f"! {cubemx_cmake} not found; using default STM32 type")
            return "STM32F405xx"

        try:
            content = cubemx_cmake.read_text(encoding="utf-8")
        except Exception:
            print(f"! Failed to read {cubemx_cmake}; using default STM32 type")
            return "STM32F405xx"

        # target_compile_definitions 内の STM32xxxxx パターンを探す
        # 例: STM32F405xx, STM32G431xx, STM32H7xx など
        pattern = (
            r"target_compile_definitions\([^)]+\bINTERFACE\s+[^)]*?(STM32[A-Z0-9]+xx)"
        )
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)

        if match:
            stm32_type = match.group(1)
            print(f"✓ Detected STM32 type: {stm32_type}")
            return stm32_type
        else:
            print(f"! STM32 type not found in {cubemx_cmake}; using default")
            return "STM32F405xx"

    def run(self, profiles: list[str] = []):
        """すべての更新を実行"""
        print("Starting project setup...\n")

        # ベース設定で更新
        self.update_cmake_lists(config=CONFIG)
        self.update_cmake_extra(config=CONFIG)
        
        # プロファイル設定をマージ
        merged_config = CONFIG.copy()
        for profile in profiles:
            profile_cfg = CONFIG.get("profiles", {}).get(profile, {})
            if not profile_cfg:
                print(f"! Profile '{profile}' not found; skipping.")
                continue
            print(f"\nApplying profile: {profile}")
            # プロファイルごとに CMakeLists.txt を更新
            self.update_cmake_presets(config={**CONFIG, **profile_cfg})
            self.update_cmake_lists(config={**CONFIG, **profile_cfg})
            self.update_cmake_extra(config={**CONFIG, **profile_cfg})
            
            # source_file_injectionsをマージ
            if "source_file_injections" in profile_cfg:
                if "source_file_injections" not in merged_config:
                    merged_config["source_file_injections"] = []
                merged_config["source_file_injections"].extend(profile_cfg["source_file_injections"])
        
        copied_files = self.copy_resources(config=CONFIG)
        # resources を実行ディレクトリへ展開した後、projectname の置換等を行う（コピーされたファイルのみ）
        self.post_process_projectname(Path.cwd(), copied_files, config=merged_config)

        print("\n✓ All files updated successfully!")


def main():
    """メイン処理"""
    argparser = argparse.ArgumentParser(description="STM32 CMake Project Setup Script")
    for profile in CONFIG.get("profiles", {}).keys():
        argparser.add_argument(
            f"--{profile}", action="store_true", help=f'Apply the "{profile}" profile'
        )
    args = argparser.parse_args()
    profiles = []
    for profile in CONFIG.get("profiles", {}).keys():
        if getattr(args, profile):
            profiles.append(profile)

    # スクリプトのディレクトリ（プロジェクトルート）を取得
    root_dir = Path.cwd()

    setup = ProjectSetup(root_dir)
    setup.run(profiles=profiles)


if __name__ == "__main__":
    main()
