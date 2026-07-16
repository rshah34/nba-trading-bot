import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NBA Trading Bot — Model Dashboard",
  description:
    "Pre-game win-probability model vs. the betting market: calibration, accuracy, and edge.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // Set the theme before first paint so there's no flash of the wrong mode.
  const themeScript = `(function(){try{var t=localStorage.getItem('theme');if(t!=='light'&&t!=='dark'){t=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}document.documentElement.setAttribute('data-theme',t);}catch(e){}})();`;

  return (
    // The inline script below sets data-theme on <html> before hydration, so the
    // client DOM intentionally differs from the server HTML on this one element.
    // suppressHydrationWarning tells React that mismatch is expected here.
    <html lang="en" className="h-full antialiased" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-full">{children}</body>
    </html>
  );
}
