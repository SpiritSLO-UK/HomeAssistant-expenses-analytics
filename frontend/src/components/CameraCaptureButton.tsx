import { useRef } from "react";

/**
 * A dedicated "take a photo" button backed by a hidden camera-capture input.
 *
 * Why separate from the normal file picker: putting `capture` on an input whose
 * `accept` also allows PDFs/CSVs makes phones either force camera-only (you can
 * no longer pick an existing file) or ignore the camera entirely and open the
 * file browser. A standalone `accept="image/*" capture="environment"` input
 * reliably opens the rear camera. On desktop `capture` is ignored, so this just
 * opens a normal file dialog — harmless.
 */
export default function CameraCaptureButton({
  onCapture,
  disabled = false,
  label = "📷 Take photo",
  className = "btn btn--ghost",
}: Readonly<{
  onCapture: (file: File) => void;
  disabled?: boolean;
  label?: string;
  className?: string;
}>) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={ref}
        type="file"
        accept="image/*"
        capture="environment"
        style={{ display: "none" }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onCapture(f);
          e.target.value = "";
        }}
      />
      <button type="button" className={className} disabled={disabled} onClick={() => ref.current?.click()}>
        {label}
      </button>
    </>
  );
}
