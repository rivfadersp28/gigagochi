"use client";

import * as AlertDialog from "@radix-ui/react-alert-dialog";

import { Button } from "@/components/ui/button";

type ConfirmActionDialogProps = {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
};

export function ConfirmActionDialog({
  open,
  title,
  description,
  confirmLabel,
  onOpenChange,
  onConfirm,
}: ConfirmActionDialogProps) {
  return (
    <AlertDialog.Root open={open} onOpenChange={onOpenChange}>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/35" />
        <AlertDialog.Content className="fixed bottom-[max(20px,var(--tma-safe-bottom))] left-1/2 z-50 grid w-[min(420px,calc(100vw-32px))] -translate-x-1/2 gap-4 rounded-xl border border-border bg-background p-5 text-foreground shadow-xl outline-none sm:bottom-auto sm:top-1/2 sm:-translate-y-1/2">
          <div className="grid gap-2">
            <AlertDialog.Title className="text-balance text-lg font-semibold">
              {title}
            </AlertDialog.Title>
            <AlertDialog.Description className="text-pretty text-sm leading-5 text-muted-foreground">
              {description}
            </AlertDialog.Description>
          </div>
          <div className="flex justify-end gap-2">
            <AlertDialog.Cancel asChild>
              <Button type="button" variant="outline">
                Отменить
              </Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action asChild>
              <Button type="button" variant="destructive" onClick={onConfirm}>
                {confirmLabel}
              </Button>
            </AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
