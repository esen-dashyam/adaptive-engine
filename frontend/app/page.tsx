"use client";

import Link from "next/link";

export default function HomePage() {
  return (
    <div className="min-h-[80vh] flex flex-col items-center justify-center">
      <div className="text-center mb-12 max-w-xl">
        <div className="w-16 h-16 bg-indigo-600 rounded-2xl flex items-center justify-center mx-auto mb-6">
          <span className="text-white text-2xl font-bold">L</span>
        </div>
        <h1 className="text-4xl font-bold text-slate-900 mb-3 tracking-tight">
          Welcome to Evlin
        </h1>
        <p className="text-slate-500 text-lg">
          Adaptive learning that meets every student exactly where they are.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5 w-full max-w-2xl">
        {/* Student card */}
        <Link href="/assessment" className="group bg-white border border-slate-200 rounded-2xl p-8 hover:border-indigo-300 hover:shadow-md transition-all text-left">
          <div className="w-12 h-12 bg-indigo-50 rounded-xl flex items-center justify-center mb-4 group-hover:bg-indigo-100 transition-colors">
            <svg className="w-6 h-6 text-indigo-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
            </svg>
          </div>
          <h2 className="text-xl font-bold text-slate-900 mb-1">I&apos;m a Student</h2>
          <p className="text-slate-500 text-sm">Take an adaptive assessment, get personalized exercises, and chat with your AI tutor.</p>
          <div className="mt-5 flex items-center gap-1.5 text-indigo-600 text-sm font-semibold group-hover:gap-3 transition-all">
            Start assessment
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
          </div>
        </Link>

        {/* Parent card */}
        <Link href="/parent" className="group bg-white border border-slate-200 rounded-2xl p-8 hover:border-emerald-300 hover:shadow-md transition-all text-left">
          <div className="w-12 h-12 bg-emerald-50 rounded-xl flex items-center justify-center mb-4 group-hover:bg-emerald-100 transition-colors">
            <svg className="w-6 h-6 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          </div>
          <h2 className="text-xl font-bold text-slate-900 mb-1">I&apos;m a Parent</h2>
          <p className="text-slate-500 text-sm">See how your child is doing, where they need support, and what you can do to help at home.</p>
          <div className="mt-5 flex items-center gap-1.5 text-emerald-600 text-sm font-semibold group-hover:gap-3 transition-all">
            View my child&apos;s report
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
          </div>
        </Link>
      </div>

      <p className="text-xs text-slate-400 mt-10">
        Grades 1–8 · Mathematics & English Language Arts · All 50 US state standards
      </p>
    </div>
  );
}
