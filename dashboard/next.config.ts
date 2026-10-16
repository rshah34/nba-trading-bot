import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The projections page used to be called the screener; old links still land on it.
  async redirects() {
    return [{ source: "/screener", destination: "/projections", permanent: true }];
  },
};

export default nextConfig;
