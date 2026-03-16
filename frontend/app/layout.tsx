import './globals.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'LeadDataScraper | CRM & Audit Dashboard',
  description: 'AI-Powered Lead Generation and Website Auditing Tool',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        {children}
      </body>
    </html>
  );
}
