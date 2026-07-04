/* eslint-disable @next/next/no-img-element */
import { resolveImageUrl } from "@/lib/api";

type PetImageProps = {
  imageUrl: string | null;
  label: string;
};

export function PetImage({ imageUrl, label }: PetImageProps) {
  const resolvedUrl = resolveImageUrl(imageUrl);

  return (
    <div className="grid place-items-center">
      {resolvedUrl ? (
        <img
          src={resolvedUrl}
          alt={label}
          className="max-h-[54vh] w-[min(74vw,560px)] object-contain sm:max-h-[60vh]"
          width={640}
          height={640}
        />
      ) : (
        <span className="text-sm text-[var(--ink-muted)]">No image</span>
      )}
    </div>
  );
}
