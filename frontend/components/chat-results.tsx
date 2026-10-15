import { Notice } from "@/components/ui";
import type { ChatBlock } from "@/lib/api";

// Only Pexels photos (photos-mcp) are drawn as pictures: loading an image from any other address
// a tool returned would tell that address who looked at the results, and when.
const PEXELS_PHOTO = /^https:\/\/images\.pexels\.com\/\S+$/;

function Block({ block }: { block: ChatBlock }) {
  return (
    <section aria-label={block.title} className="arrive">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="font-display text-lg font-semibold">{block.title}</h3>
        {/* Every result says which tool it came from. */}
        <p className="text-xs text-ink-soft">From {block.source}</p>
      </div>
      {block.kind === "notice" && <Notice tone={block.tone ?? "info"}>{block.text}</Notice>}
      {block.kind === "table" && (
        <div className="overflow-x-auto rounded-xl border border-line">
          <table className="w-full text-left text-sm">
            <thead className="bg-canvas text-xs text-ink-soft">
              <tr>
                {block.columns?.map((column) => (
                  <th key={column} scope="col" className="px-3 py-2 font-medium whitespace-nowrap">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows?.map((row, index) => (
                <tr key={index} className="border-t border-line">
                  {row.map((cell, column) => (
                    <td key={column} className="px-3 py-2 align-top">
                      {typeof cell === "string" && PEXELS_PHOTO.test(cell) ? (
                        // alt="": the photo's description is in the same row.
                        // eslint-disable-next-line @next/next/no-img-element -- a thumbnail; next/image would proxy it and need remotePatterns
                        <img src={cell} alt="" loading="lazy" referrerPolicy="no-referrer" className="h-16 w-16 rounded-lg object-cover" />
                      ) : (
                        cell
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {block.kind === "card" && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-5 gap-y-1.5 rounded-xl border border-line p-4 text-sm">
          {block.fields?.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-ink-soft">{label}</dt>
              <dd className="break-words">{value === "" ? "—" : value}</dd>
            </div>
          ))}
        </dl>
      )}
      {block.kind === "raw" && (
        <pre className="overflow-x-auto rounded-xl bg-canvas p-3 font-mono text-xs break-words whitespace-pre-wrap">{block.text}</pre>
      )}
    </section>
  );
}

/** The results of the agent's last answer, in plain form: one block per tool call, then the calls in words. */
export function ChatResults({ blocks, steps }: { blocks: ChatBlock[]; steps: string[] }) {
  if (blocks.length === 0) {
    return (
      <div className="m-auto max-w-sm text-center">
        <p className="font-display text-xl font-semibold">Results show up here</p>
        <p className="mt-1 text-ink-soft">
          When the agent looks something up or changes something, you see it here as a table or a card, with the tool it came
          from.
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-6">
      {blocks.map((block, index) => (
        <Block key={`${block.call_id}-${index}`} block={block} />
      ))}
      {/* An answer that called no tool has no steps; it can still have the list of the chat's earlier calls above. */}
      {steps.length > 0 && (
        <section aria-label="How this was found" className="border-t border-line pt-4">
          <h3 className="text-xs font-medium text-ink-soft">How this was found</h3>
          <ol className="mt-1.5 list-inside list-decimal space-y-0.5 text-sm">
            {steps.map((step, index) => (
              <li key={index}>{step}</li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}
