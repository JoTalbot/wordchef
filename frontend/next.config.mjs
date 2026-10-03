const isAndroidExport = process.env.WORDCHEF_ANDROID === '1';

const nextConfig = {
  output: 'export',
  images: { unoptimized: true },
  trailingSlash: true,
  ...(isAndroidExport ? { assetPrefix: './' } : {}),
};

export default nextConfig;
