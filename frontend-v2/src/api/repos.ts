import { api } from "./client";
export interface ReposResponse { items: string[]; }
export const listRepos = () => api.get<ReposResponse>("/v1/repos").then((r) => r.data);
