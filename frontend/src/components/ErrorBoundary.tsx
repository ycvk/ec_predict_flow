import React from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

type Props = {
  children: React.ReactNode;
};

type State = {
  error: Error | null;
  errorInfo: React.ErrorInfo | null;
};

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null, errorInfo: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    this.setState({ error, errorInfo });
    // 仍保留浏览器控制台信息，便于排障
    // eslint-disable-next-line no-console
    console.error("[EC Predict Flow] UI crashed:", error, errorInfo);
  }

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div className="mx-auto max-w-3xl p-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">页面渲染失败</CardTitle>
            <CardDescription>前端发生运行时错误，导致页面无法正常显示。</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3">
            <div className="text-sm">
              <div className="font-medium">错误信息</div>
              <div className="mt-1 rounded-md border bg-muted/40 p-3 font-mono text-xs">
                {this.state.error?.message || String(this.state.error)}
              </div>
            </div>
            <Separator />
            <div className="text-sm text-muted-foreground">
              建议打开浏览器 DevTools → Console，复制错误堆栈给我，我可以进一步定位并修复。
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }
}

