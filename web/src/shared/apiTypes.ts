/**
 * 工作台写接口的统一调用签名，由应用装配层注入具体实现。
 * 泛型 T 表示接口返回的业务数据类型；path 为同源 API 路径，
 * method 省略时由实现使用默认方法，payload 为请求体。
 * 返回通过基础结构校验后的业务数据。
 */
export type ApiClient = <T>(path: string, method?: string, payload?: unknown) => Promise<T>

/**
 * 工作台读接口的统一调用签名，只接受路径并返回业务数据类型。
 * 泛型 T 表示接口返回的业务数据类型；path 为同源 API 路径。
 * 返回通过基础结构校验后的业务数据。
 */
export type ApiReader = <T>(path: string) => Promise<T>

/**
 * 游标分页的通用返回结构，用于轨迹、审计等需要连续翻页的列表。
 * items 为当前页数据；next_cursor 为下一页游标，为 null 表示已到末尾。
 */
export interface CursorPage<T> {
  items: T[]
  next_cursor: string | null
}

/**
 * 不分页的列表返回结构，用于一次返回全部数据的接口。
 * items 为列表数据。
 */
export interface Items<T> {
  items: T[]
}

/**
 * 新建资源后的最小返回结构，用于需要后续读取详情的写接口。
 * id 为新资源的唯一标识。
 */
export interface CreatedResource {
  id: string
}
