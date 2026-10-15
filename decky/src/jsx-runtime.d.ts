// React 由 Decky 运行时以全局 SP_REACT / SP_JSX 提供，不随插件打包。
// @types/react 16 没有 react/jsx-runtime 的类型，这里补一个最小的声明，
// 让 tsc（jsx: react-jsx）能通过；实际构建产物会被 @decky/rollup 映射成 SP_JSX。
declare module "react/jsx-runtime" {
  export const Fragment: unknown;
  export function jsx(type: unknown, props: unknown, key?: unknown): unknown;
  export function jsxs(type: unknown, props: unknown, key?: unknown): unknown;
  export function jsxDEV(type: unknown, props: unknown, key?: unknown): unknown;
}
