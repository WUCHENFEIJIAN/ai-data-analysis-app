import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider } from "@/features/settings/theme-provider";

export const metadata: Metadata = {
  title: "Data Studio",
  description: "AI-assisted data analysis workspace",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: `try{var m=localStorage.getItem("data-studio-theme");var d=m==="dark"||m==="light"?m:(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");document.documentElement.dataset.theme=d;document.documentElement.style.colorScheme=d}catch(e){}` }} />
      </head>
      <body><ThemeProvider>{children}</ThemeProvider></body>
    </html>
  );
}
