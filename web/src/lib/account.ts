/** 与后端新建账号、改密规则保持一致的前端校验。 */

export const USERNAME_RE = /^[A-Za-z][A-Za-z0-9._-]{2,31}$/;

export function passwordIssue(password: string, username = ""): string | null {
  if (!password) return "请填写密码";
  if (password !== password.trim()) return "密码首尾不能包含空格";
  if (password.length < 8) return "密码不少于 8 位";
  if (!/[A-Za-z]/.test(password) || !/\d/.test(password)) return "密码须同时包含字母与数字";
  if (username && password.toLowerCase() === username.trim().toLowerCase()) return "密码不能与用户名相同";
  return null;
}

export function createAccountErrors(form: { username: string; display_name: string; password: string; password_confirm: string; role: string }): Record<string, string> {
  const errors: Record<string, string> = {};
  const username = form.username.trim();
  const displayName = form.display_name.trim();
  if (!username) errors.username = "请填写用户名";
  else if (!USERNAME_RE.test(username)) errors.username = "以字母开头，3–32 位，仅字母、数字、点、下划线或短横线。例如 zhangsan";
  if (!displayName) errors.display_name = "请填写姓名";
  else if (displayName.length > 32) errors.display_name = "姓名不超过 32 个字";
  const issue = passwordIssue(form.password, username);
  if (issue) errors.password = issue === "请填写密码" ? "请填写初始密码" : issue;
  if (!form.password_confirm) errors.password_confirm = "请再次输入初始密码";
  else if (form.password_confirm !== form.password) errors.password_confirm = "两次输入的密码不一致";
  if (!form.role) errors.role = "请选择基础角色";
  return errors;
}
