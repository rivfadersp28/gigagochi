import type { Metadata } from "next";
import localFont from "next/font/local";
import Script from "next/script";

import { TelegramBootstrap } from "@/components/TelegramBootstrap";
import { APP_BACKGROUND_COLOR } from "@/lib/theme";

import "./globals.css";

const openRunde = localFont({
  src: [
    {
      path: "./fonts/open-runde/OpenRunde-Regular.woff2",
      weight: "400",
      style: "normal",
    },
    {
      path: "./fonts/open-runde/OpenRunde-Medium.woff2",
      weight: "500",
      style: "normal",
    },
    {
      path: "./fonts/open-runde/OpenRunde-Semibold.woff2",
      weight: "600",
      style: "normal",
    },
    {
      path: "./fonts/open-runde/OpenRunde-Bold.woff2",
      weight: "700",
      style: "normal",
    },
  ],
  variable: "--font-open-runde",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AI Tamagotchi",
  description: "Local AI Tamagotchi MVP",
};

const backgroundBootstrapScript = `
(function () {
  var color = "${APP_BACKGROUND_COLOR}";
  document.documentElement.style.backgroundColor = color;
  if (document.body) {
    document.body.style.backgroundColor = color;
  }

  function applyTelegramColors() {
    var webApp = window.Telegram && window.Telegram.WebApp;
    if (!webApp || !webApp.initData) return;

    try {
      if (webApp.setHeaderColor) webApp.setHeaderColor(color);
      if (webApp.setBackgroundColor) webApp.setBackgroundColor(color);
      if (webApp.setBottomBarColor) webApp.setBottomBarColor(color);
    } catch (error) {
      // Telegram color APIs are optional across clients.
    }
  }

  applyTelegramColors();
  document.addEventListener("DOMContentLoaded", function () {
    document.body.style.backgroundColor = color;
    applyTelegramColors();
  });
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ru"
      className={`${openRunde.variable} dark h-full antialiased`}
      style={{ backgroundColor: APP_BACKGROUND_COLOR }}
      suppressHydrationWarning
    >
      <body className="min-h-full flex flex-col" style={{ backgroundColor: APP_BACKGROUND_COLOR }}>
        <Script src="https://telegram.org/js/telegram-web-app.js" strategy="beforeInteractive" />
        <Script
          id="app-background-bootstrap"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: backgroundBootstrapScript }}
        />
        <TelegramBootstrap />
        {children}
      </body>
    </html>
  );
}
