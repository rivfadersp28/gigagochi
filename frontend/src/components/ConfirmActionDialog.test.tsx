import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmActionDialog } from "./ConfirmActionDialog";

describe("ConfirmActionDialog", () => {
  it("announces the consequence and confirms the action", () => {
    const onConfirm = vi.fn();

    render(
      <ConfirmActionDialog
        open
        title="Сбросить параметры?"
        description="Голод, настроение и здоровье станут равны нулю."
        confirmLabel="Сбросить"
        onOpenChange={() => undefined}
        onConfirm={onConfirm}
      />,
    );

    expect(screen.getByRole("alertdialog")).toHaveAccessibleName("Сбросить параметры?");
    expect(screen.getByText("Голод, настроение и здоровье станут равны нулю.")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Сбросить" }));

    expect(onConfirm).toHaveBeenCalledOnce();
  });
});
