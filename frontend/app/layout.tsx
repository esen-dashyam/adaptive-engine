import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Evlin Learning",
  description: "Adaptive K1-K8 learning powered by AI",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-slate-50 flex flex-col">
        <nav className="bg-white border-b border-slate-200 px-6 py-0 sticky top-0 z-50">
          <div className="max-w-6xl mx-auto flex items-center justify-between h-14">
            <a href="/" className="flex items-center gap-2.5">
              <div className="w-7 h-7 bg-indigo-600 rounded-lg flex items-center justify-center">
                <span className="text-white text-xs font-bold">L</span>
              </div>
              <span className="font-bold text-slate-900 text-base tracking-tight">Evlin</span>
            </a>
            <div className="flex items-center gap-1 text-sm">
              <a href="/assessment"
                className="px-3 py-1.5 rounded-lg text-slate-600 hover:bg-slate-100 hover:text-slate-900 transition-colors font-medium">
                Student
              </a>
              <a href="/parent"
                className="px-3 py-1.5 rounded-lg text-slate-600 hover:bg-slate-100 hover:text-slate-900 transition-colors font-medium">
                Parent
              </a>
              <a href="/tutor"
                className="px-3 py-1.5 rounded-lg text-slate-600 hover:bg-slate-100 hover:text-slate-900 transition-colors font-medium">
                AI Tutor
              </a>
              <a href="/dashboard"
                className="px-3 py-1.5 rounded-lg text-slate-600 hover:bg-slate-100 hover:text-slate-900 transition-colors font-medium">
                Dashboard
              </a>
            </div>
          </div>
        </nav>
        <main className="flex-1 max-w-6xl w-full mx-auto px-6 py-8">{children}</main>
        <footer className="border-t border-slate-200 py-4 text-center text-xs text-slate-400">
          Powered by Neo4j · Bayesian Knowledge Tracing · Google Gemini
        </footer>
      </body>
    </html>
  );
}
