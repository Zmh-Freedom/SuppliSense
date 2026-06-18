export default function SourcingPage() {
  return (
    <div className="flex-1 flex items-center justify-center px-4">
      <div className="text-center space-y-4 max-w-sm">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-[var(--color-primary-bg)]/10">
          <svg className="w-8 h-8 text-[var(--color-primary-bg)]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10"/>
            <circle cx="12" cy="12" r="3"/>
            <path d="M12 2v4M12 18v4M2 12h4M18 12h4"/>
          </svg>
        </div>
        <h2 className="text-xl font-bold text-[var(--color-text)]">智能寻源</h2>
        <p className="text-sm text-gray-500 leading-relaxed">
          基于采购需求自动匹配最优供应商，综合成本、质量、产能、技术等多维度打分排序，敬请期待
        </p>
        <button
          disabled
          className="bg-[var(--color-primary-bg)]/50 text-white rounded-xl px-6 py-2.5 text-sm cursor-not-allowed"
          title="功能开发中"
        >
          提交采购需求
        </button>
      </div>
    </div>
  );
}
