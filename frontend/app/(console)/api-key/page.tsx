"use client";

import { KeyManager } from "@/components/key-manager";
import { Card, PageTitle } from "@/components/ui";

export default function ApiKeyPage() {
  return (
    <>
      <PageTitle title="Your stores, from your editor.">
        Paste this key into an MCP client such as Claude Code or Cursor to reach your store from there. You can
        have one active key at a time. To replace it, revoke it and issue a new one.
      </PageTitle>
      <Card className="max-w-2xl">
        <KeyManager basePath="/team/me/api-keys" />
      </Card>
    </>
  );
}
