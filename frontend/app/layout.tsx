import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Adaptive Learning Engine",
  description: "K1-K8 adaptive assessments powered by Neo4j + BKT + Gemini AI",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-gray-50">
        <nav className="bg-white border-b border-gray-200 px-6 py-4">
          <div className="max-w-6xl mx-auto flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
                <span className="text-white text-sm font-bold">AE</span>
              </div>
              <span className="font-semibold text-gray-900 text-lg">Adaptive Learning Engine</span>
              <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">K1–K8</span>
            </div>
            <div className="flex items-center gap-6 text-sm">
              <a href="/" className="text-gray-600 hover:text-blue-600 transition-colors font-medium">Home</a>
              <a href="/assessment" className="text-gray-600 hover:text-blue-600 transition-colors font-medium">Assessment</a>
              <a href="/dashboard" className="text-gray-600 hover:text-blue-600 transition-colors font-medium">Dashboard</a>
              <a href="/rasch" className="text-gray-600 hover:text-blue-600 transition-colors font-medium">Diagnostic</a>
              <a
                href="http://localhost:8000/docs"
                target="_blank"
                rel="noopener noreferrer"
                className="text-gray-500 hover:text-blue-600 transition-colors"
              >
                API Docs
              </a>
            </div>
          </div>
        </nav>
        <main className="max-w-6xl mx-auto px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
