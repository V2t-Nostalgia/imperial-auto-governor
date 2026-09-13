# 发布流程

`v0.5.10` 按本流程构建并验证正式附件。默认先创建 GitHub Draft Release；发布者明确授权正式发布时，可在全部本地验收通过后直接创建非 Draft Release。

1. 审阅完整源码和 `git diff`。
2. 运行 `python scripts/validation/run_tests.py`、编译、静态检查和隐私扫描。
3. 提交并推送源码；提交必须带 DCO sign-off。
4. 从该提交重新构建 Windows Agent、Host Bridge 和 Ubuntu Agent，不复用旧 EXE/ZIP。
5. 对每个解压包运行健康检查并验证包内 manifest 与 SHA-256。
6. 由发布者创建签名 annotated tag `v0.5.10`。
7. 创建 GitHub Release，上传以下五个附件；未获正式发布授权时保持 Draft：

   - `ImperialAutoGovernor-0.5.10-source.zip`
   - `IAGWindowsAgent-0.5.10-windows-x64.zip`
   - `IAGHostBridge-0.5.10-windows-x64.zip`
   - `IAGUbuntuAgent-0.5.10-linux-x86_64.tar.gz`
   - `SHA256SUMS-0.5.10.txt`

8. 从 GitHub 重新下载附件并核对顶层 SHA-256。
9. 若使用 Draft 验收，在代理模式的整局联机测试通过后再转为正式 Release。
10. 正式发布后由发布者自行登记 Zenodo DOI 与 Software Heritage SWHID。

签名只能使用发布者自己的长期 GPG 密钥。构建脚本不得创建临时密钥，也不得自动把 Draft 转成正式发布。
