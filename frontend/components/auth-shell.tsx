/** Frame for the signed-out pages: the aurora on one side, the form on the other. */
export function AuthShell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen bg-white lg:grid-cols-2">
      <div className="aurora relative m-3 flex min-h-56 flex-col justify-end overflow-hidden rounded-3xl p-8 text-white lg:p-12">
        <span />
        <span />
        <span />
        <span />
        <span />
        {/* Darkens the foot of the panel so the white text stays readable as the colours move. */}
        <div className="absolute inset-0 bg-linear-to-t from-[#3a0f6e]/75 to-transparent to-60%" />
        <p className="relative font-display text-6xl font-semibold tracking-tight lg:text-8xl">Urumi</p>
        <p className="relative mt-3 max-w-sm text-lg text-white/90">
          Your stores, your team, and what each person can reach.
        </p>
      </div>
      <main className="flex items-center justify-center p-8 lg:p-14">
        <div className="arrive w-full max-w-sm">
          <h1 className="font-display text-3xl font-semibold tracking-tight">{title}</h1>
          <div className="mt-6 space-y-4">{children}</div>
        </div>
      </main>
    </div>
  );
}
