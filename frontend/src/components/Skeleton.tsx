export default function Skeleton({ className = '' }: { className?: string }) {
  return (
    <div
      className={`bg-[#e8e8e3] rounded-lg animate-[skeleton-pulse_1.5s_ease-in-out_infinite] ${className}`}
      aria-hidden="true"
    />
  );
}

export function SkeletonCard({ className = '' }: { className?: string }) {
  return (
    <div className={`bg-white border border-[#e8e8e3] rounded-2xl p-4 shadow-sm ${className}`}>
      <Skeleton className="h-4 w-3/4 mb-3" />
      <Skeleton className="h-3 w-1/2 mb-2" />
      <Skeleton className="h-3 w-2/3" />
    </div>
  );
}

export function SkeletonChart({ className = '' }: { className?: string }) {
  return (
    <div className={`bg-white border border-[#e8e8e3] rounded-2xl p-5 shadow-sm ${className}`}>
      <Skeleton className="h-4 w-1/3 mb-4" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}
