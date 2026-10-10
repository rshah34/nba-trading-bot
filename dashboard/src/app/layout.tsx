import type { Metadata } from "next";
import { Nav } from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "NBA Trading Bot",
  description:
    "Tonight's slate, player and team stats, prop projections, injury news, and the bot's picks.",
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
      <body className="min-h-full">
        <Nav />
        <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6">{children}</main>
      </body>
    </html>
  );
}
