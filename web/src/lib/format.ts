export function amount(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)} 亿元`;
  if (abs >= 1e4) return `${(v / 1e4).toFixed(digits)} 万元`;
  return `${v.toFixed(0)} 元`;
}

export function pct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function parse(ts: string | null | undefined): Date | null {
  if (!ts) return null;
  // 后端时间均为 UTC：不带时区标记的 ISO 时间按 UTC 解析，再按浏览器所在时区显示
  const s = /^\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d(\.\d+)?)?$/.test(ts) ? ts.replace(" ", "T") + "Z" : ts;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function dateTime(ts: string | null | undefined): string {
  const d = parse(ts);
  if (!d) return "—";
  return d.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

export function date(ts: string | null | undefined): string {
  const d = parse(ts);
  if (!d) return "—";
  return d.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

export function relative(ts: string | null | undefined): string {
  const d = parse(ts);
  if (!d) return "—";
  const diff = (Date.now() - d.getTime()) / 1000;
  const future = diff < 0;
  const a = Math.abs(diff);
  const unit = a < 60 ? `${Math.round(a)} 秒` : a < 3600 ? `${Math.round(a / 60)} 分钟` : a < 86400 ? `${Math.round(a / 3600)} 小时` : `${Math.round(a / 86400)} 天`;
  return future ? `${unit}后` : `${unit}前`;
}

export function formatSeconds(s: number): string {
  const n = Math.max(0, s);
  if (n < 60) return `${n.toFixed(0)} 秒`;
  if (n < 3600) return `${Math.floor(n / 60)} 分 ${Math.round(n % 60)} 秒`;
  return `${Math.floor(n / 3600)} 小时 ${Math.round((n % 3600) / 60)} 分`;
}

export function duration(start?: string | null, end?: string | null): string {
  const a = parse(start);
  if (!a) return "—";
  const b = parse(end) || new Date();
  return formatSeconds(Math.max(0, (b.getTime() - a.getTime()) / 1000));
}

/** 仅统计执行中（规划/运行）的用时；排队与等人审不计。 */
export function runDuration(
  run?: {
    duration_s?: number | null;
    status?: string | null;
    active_since?: string | null;
    usage?: { active_seconds?: number | null; active_since?: string | null } | null;
    started_at?: string | null;
    created_at?: string | null;
    ended_at?: string | null;
  } | null,
  nowMs: number = Date.now(),
): string {
  if (!run) return "—";
  if (run.status === "queued" && !run.started_at) return "—";
  const active = run.status === "planning" || run.status === "running";
  const since = run.active_since || run.usage?.active_since;
  const frozen = run.usage?.active_seconds;
  if (active && since != null && frozen != null) {
    const a = parse(since);
    if (a) return formatSeconds(Number(frozen) + Math.max(0, (nowMs - a.getTime()) / 1000));
  }
  if (run.duration_s != null && !Number.isNaN(Number(run.duration_s))) {
    return formatSeconds(Number(run.duration_s));
  }
  return duration(run.started_at || run.created_at, run.ended_at);
}

export const AGENTS: Record<string, { name: string; color: string; short: string }> = {
  "AG-RA": { name: "风险评估", short: "评估", color: "bg-violet-500" },
  "AG-CD": { name: "条款设计", short: "条款", color: "bg-indigo-500" },
  "AG-AP": { name: "精算定价", short: "定价", color: "bg-sky-500" },
  "AG-UW": { name: "承保风控", short: "核保", color: "bg-teal-500" },
  "AG-RW": { name: "风险预警", short: "预警", color: "bg-amber-500" },
  "AG-CL": { name: "定损理赔", short: "理赔", color: "bg-rose-500" },
};

export function agentLabel(id?: string | null) {
  if (!id) return "";
  return AGENTS[id]?.name || id;
}

export const LINE_NAMES: Record<string, string> = {
  RD01: "科技研发转化综合保险",
  RD02: "概念验证与小试综合费用损失保险",
  RD03: "中试验证综合费用损失保险",
  RD04: "研发费用损失保险（全国）",
  RD05: "陕西省科技项目研发费用损失保险",
  RD06: "深圳市科技企业普惠型研发费用损失保险",
  RD07: "产品研发责任保险",
  RD08: "关键研发设备保险",
  RD09: "研发中断研发费用补偿保险",
  RD10: "深圳市科技企业普惠型研发中断费用保险",
  RD11: "高新技术企业关键研发设备保险",
  RD12: "科研仪器开放共享损失补偿保险",
  TR01: "科技成果转化费用损失保险",
  TR02: "科技成果先用后转履约保证保险",
  TR08: "首台（套）重大技术装备综合保险",
  TR03: "首台套重大技术装备责任保险",
  TR04: "首台套重大技术装备质量保证保险",
  TR05: "重点新材料首批次应用综合保险",
  TR06: "首批次新材料质量保证保险",
  TR07: "首版次软件综合保险",
  IP01: "专利申请费用补偿保险",
  IP02: "宁波市PCT国际专利申请费用补偿保险",
  IP03: "专利执行保险",
  IP04: "专利被侵权损失保险",
  IP05: "深圳市科技企业普惠型专利被侵权损失保险",
  IP06: "侵犯专利权责任保险",
  IP07: "知识产权海外侵权责任保险",
  IP08: "商业秘密被侵权损失保险",
  IP09: "商业秘密被侵权维权费用补偿保险",
  CY01: "网络安全保险",
  AI01: "人工智能产品责任保险",
  AI02: "智能控制系统责任保险",
  LS03: "临床试验责任险",
  LS01: "生命科学产品完工责任保险",
  LS02: "疫苗安全责任保险",
  AV01: "卫星发射前保险",
  AV02: "卫星在轨寿命保险",
  AV03: "卫星发射及初始运行保险",
  AV04: "航空器机身一切险及责任保险",
  AV05: "航空器机身战争及相关风险保险",
  AV06: "无人机保险",
};

export const STAGE_NAMES: Record<string, string> = { rd: "研发", transfer: "成果转化", industrial: "产业化", mixed: "多阶段" };

export const METHOD_NAMES: Record<string, string> = {
  DM1: "关键风险指标规则",
  DM2: "时序异常检测",
  DM3: "变点检测",
  DM4: "事件抽取与舆情研判",
  DM5: "风险传导图谱",
  TP1: "设备健康检测",
  TP2: "数据安全检测",
  TP3: "研发进度偏离检测",
  TP4: "知识产权状态检测",
  TP5: "产品质量信号检测",
};

/** 产品库/费率组件里的定价方法：可能是编码字符串，也可能是 {name, suggested_rate} 对象 */
export const PRICING_METHOD_NAMES: Record<string, string> = {
  weighted: "创新积分制定价法",
  maturity: "纯风险损失率法（旧方法）",
  classification: "分类费率厘定法",
  PM1: "广义线性模型",
  PM2: "梯度提升树 + Shapley",
  PM3: "信度加权",
  PM4: "风险成本加成法",
};

/** 旧产物里方法一曾叫「赋权综合评分定价法」，现行名称为「创新积分制定价法」。 */
const LEGACY_METHOD_ALIAS: Record<string, string> = { 赋权综合评分定价法: "创新积分制定价法" };

export function pricingMethodLabel(method: unknown): string {
  if (method == null || method === "") return "—";
  if (typeof method === "string") return LEGACY_METHOD_ALIAS[method] || PRICING_METHOD_NAMES[method] || METHOD_NAMES[method] || method;
  if (typeof method === "object") {
    const row = method as { name?: string; method?: string; key?: string };
    if (row.name) return LEGACY_METHOD_ALIAS[row.name] || row.name;
    if (row.method) return pricingMethodLabel(row.method);
    if (row.key) return pricingMethodLabel(row.key);
  }
  return String(method);
}

export function formatPricingMethods(methods: unknown[] | null | undefined): string {
  if (!methods?.length) return "—";
  return methods.map(pricingMethodLabel).join("、");
}

export function lineLabel(code: string | null | undefined, fallback?: string): string {
  if (!code) return fallback || "—";
  return LINE_NAMES[code] || fallback || code;
}

export const ZONE_LABELS: Record<string, string> = { cloud: "云域", private: "私域", platform: "平台", follow: "跟随数据" };

export const MATERIAL_SOURCE: Record<string, string> = { iot: "物联网", log: "日志", ops: "运营", news: "舆情", court: "司法", supplier: "供应链", patent: "知识产权", vuln: "漏洞情报", upload: "客户上传" };
