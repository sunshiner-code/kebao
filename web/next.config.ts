import type { NextConfig } from "next";

const backend = process.env.TECHINS_API_URL || "http://127.0.0.1:8742";

// 需要额外放行的开发来源（例如通过局域网 IP 打开 dev server 时），逗号分隔
const devOrigins = (process.env.TECHINS_DEV_ORIGINS || "")
  .split(",")
  .map((item) => item.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  allowedDevOrigins: devOrigins,
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  experimental: {
    instantInsights: { validationLevel: "manual-warning" },
    webpackBuildWorker: false,
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
