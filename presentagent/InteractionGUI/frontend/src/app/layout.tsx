import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PresentAgent-2 - Presentation & Q&A System",
  description: "PresentAgent-2 - Presentation Generation & Q&A Interaction Platform",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
