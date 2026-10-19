import { markLine } from "./ui";

/** Frame for the signed-out pages: the ink panel with the mark's line on one side, the form on the other. */
export function AuthShell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="cut relative m-3 flex min-h-56 flex-col justify-end overflow-hidden bg-ink p-8 text-canvas [--cut:40px] lg:p-12">
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className="absolute -top-[10%] -right-[20%] w-[130%] text-canvas/25"
          fill="none"
          stroke="currentColor"
          strokeWidth="0.06"
          strokeLinecap="round"
        >
          <path d={markLine} />
        </svg>
        <p className="relative font-display text-6xl font-light tracking-tight lg:text-8xl">relay</p>
        <p className="relative mt-3 max-w-sm text-lg text-canvas/75">
          Your stores, your team, and what each person can reach.
        </p>
      </div>
      <main className="flex items-center justify-center p-8 lg:p-14">
        <div className="arrive w-full max-w-sm">
          <h1 className="font-display text-4xl font-light tracking-tight">{title}</h1>
          <div className="mt-8 space-y-5">{children}</div>
        </div>
      </main>
    </div>
  );
}
