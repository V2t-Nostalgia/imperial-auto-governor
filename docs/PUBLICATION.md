<!-- SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# 首次公开与长期归档

本文件是 v0.5.7 的发布清单，不声称尚未取得的签名、DOI 或 SWHID。构建脚本不会创建临时签名密钥，也不会替发布者提交源码或操作外部服务。

## 正式发布流程

1. 审阅完整源码，确认模组、Agent Runtime、`save_state`、Packet Interceptor、Windows Host Bridge 和发布脚本均属于预期公开边界。
2. 核对 `NOTICE`、`ORIGIN.md`、`CITATION.cff`、`.zenodo.json` 与 `AUTHORS.md` 中的原创者稳定网名均为 `Nostalgia`，并集中搜索确认没有残留身份占位符。
3. 运行全部测试，包括 Python 语法检查、Agent Runtime、Packet Interceptor、Windows Host Uploader，以及三个平台包的解压后健康检查。
4. 在干净目录从同一源码状态重新构建，不复用旧 EXE、旧 ZIP、虚拟环境或展开目录：

   ```bash
   python tools/release/build_all_releases.py
   ```

5. 独立验证所有包、manifest 文件列表、包内 SHA-256、隐私扫描、ZIP Slip、符号链接与解压健康检查：

   ```bash
   python tools/release/verify_release.py --directory public_release
   ```

6. 提交经过审阅的源码。正式附件的 `RELEASE_MANIFEST.json` 必须记录该提交；若清单显示 `uncommitted-review-build`，只能用于本地审阅，提交后必须重建。
7. 由发布者使用自己的长期密钥创建签名 annotated tag `v0.5.7`：

   ```bash
   git tag -s v0.5.7 -m "Imperial Auto Governor v0.5.7"
   git tag -v v0.5.7
   git push origin v0.5.7
   ```

8. 创建 GitHub Draft Release，目标必须是签名 Tag 对应的提交。
9. 上传且只上传以下五个附件：

   ```text
   ImperialAutoGovernor-0.5.7-source.zip
   IAGWindowsAgent-0.5.7-windows-x64.zip
   IAGHostBridge-0.5.7-windows-x64.zip
   IAGUbuntuAgent-0.5.7-linux-x86_64.tar.gz
   SHA256SUMS-0.5.7.txt
   ```

10. 从 GitHub Draft Release 重新下载五个附件，在独立空目录核对 `SHA256SUMS-0.5.7.txt`，并再次运行解压验证。
11. 验证无误后再把 Draft Release 正式发布。不要上传 `public_release.zip`、展开目录、重复包或本地审阅缓存。
12. 发布后由发布者创建 Zenodo DOI，并向 Software Heritage 提交规范仓库 URL；服务返回后再记录 DOI 与 SWHID，不得预填。

## 签名清单

可以额外生成 `SHA256SUMS-0.5.7.txt.asc`，但只能由项目发布者使用自己的长期密钥签署：

```bash
gpg --armor --detach-sign SHA256SUMS-0.5.7.txt
```

代码和构建脚本不得创建临时密钥冒充发布者签名。

## 可复现记录

Release 页面应记录 Git commit、签名 Tag、构建环境、Python 版本、Stellaris 兼容版本、五个附件的 SHA-256、演示视频地址，以及后续取得的 Zenodo DOI 和 SWHID。
