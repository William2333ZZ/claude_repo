"""Data-Juicer 引擎适配器(开源 Apache-2.0,薄适配)。

依赖方式:pip install py-data-juicer(可选依赖,平台不强制安装)。
适配面故意最小:只经 `dj-process --config <yaml>` 子进程调用,
配置文件用 JSON 写出(JSON 是 YAML 子集,免去 yaml 依赖)。
DJ 未安装时 check() 明确报错并给出安装指引,平台其余功能不受影响。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class DataJuicerEngine:
    name = "datajuicer"

    def available(self) -> bool:
        return shutil.which("dj-process") is not None

    def status(self) -> dict:
        if self.available():
            return {"available": True, "note": "Data-Juicer(开源 Apache-2.0),经 dj-process CLI 调用"}
        return {
            "available": False,
            "note": "未安装:pip install py-data-juicer 后即可用 engine=datajuicer 跑 DJ 配方",
        }

    def check(self, recipe: dict) -> tuple[bool, str]:
        if not self.available():
            return False, "Data-Juicer 未安装:pip install py-data-juicer(开源,Apache-2.0)"
        process = recipe.get("process")
        if not isinstance(process, list) or not process:
            return False, 'recipe 需要 {"process": [ {算子名: {参数}}, ... ]}(Data-Juicer 配方格式)'
        return True, ""

    def run(self, dataset_path: str, out_dir: str | Path, recipe: dict) -> dict:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        export_path = out / "output.jsonl"
        config = {
            "project_name": "datafoundry-dj-run",
            "dataset_path": str(dataset_path),
            "export_path": str(export_path),
            "np": int(recipe.get("np", 2)),
            "process": recipe["process"],
        }
        config_path = out / "dj_config.yaml"  # JSON 内容,YAML 兼容
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        proc = subprocess.run(
            ["dj-process", "--config", str(config_path)],
            capture_output=True,
            text=True,
            timeout=float(recipe.get("timeout", 3600)),
        )
        if proc.returncode != 0:
            raise RuntimeError(f"dj-process 退出码 {proc.returncode}: {proc.stderr[-800:]}")
        n_in = sum(1 for line in open(dataset_path, encoding="utf-8") if line.strip())
        n_out = (
            sum(1 for line in open(export_path, encoding="utf-8") if line.strip()) if export_path.exists() else 0
        )
        manifest = {
            "engine": "datajuicer",
            "dataset": str(dataset_path),
            "recipe": config["process"],
            "n_in": n_in,
            "n_out": n_out,
            "retention": round(n_out / n_in, 4) if n_in else 1.0,
            "output": str(export_path),
            "note": "DJ 引擎运行:逐样本血缘与漏斗编排仅 native 引擎提供(v0)",
        }
        with open(out / "manifest.json", "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
        return manifest
