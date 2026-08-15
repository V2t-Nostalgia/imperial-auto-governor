# Code Map

`FILE_INDEX.md` 是整个工程的逐文件入口，由 `scripts/migration/generate_code_map.py` 生成。阅读顺序建议如下：

1. 先读根目录 `README.md` 和 `docs/ARCHITECTURE.md`。
2. 按职责进入相应目录的 README。
3. 在 `FILE_INDEX.md` 查目标文件的迁移来源、职责和公开入口。
4. 修改协议代码前再读 `docs/reference/v0_5_8/PACKET_INTERCEPTOR.md`。

生成命令：

```powershell
python scripts/migration/generate_code_map.py
```
