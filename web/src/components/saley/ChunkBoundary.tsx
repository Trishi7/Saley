// Catches an error of one page, so the rail and the top bar stay (R5 T17 review). App.tsx keys
// it on the view id, so a new page starts clean. A chunk that did not load (a deploy changed
// the file names) needs a reload; any other render error gets a plain Retry.
import { Component, type ReactNode } from "react";
import { ApiError } from "@/api";
import { ErrorLine } from "@/components/saley/states";
import { isChunkLoadError } from "@/lib/chunk";
import { W } from "@/words";

type Props = { what: string; children: ReactNode };
type State = { error: unknown };

export class ChunkBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: unknown): State {
    return { error };
  }

  render() {
    const { error } = this.state;
    if (error === null) return this.props.children;
    const chunk = isChunkLoadError(error);
    const line = new ApiError(0, chunk ? W.pageUpdated : W.errGeneric);
    const retry = chunk ? () => window.location.reload() : () => this.setState({ error: null });
    return <ErrorLine what={this.props.what} error={line} onRetry={retry} />;
  }
}
