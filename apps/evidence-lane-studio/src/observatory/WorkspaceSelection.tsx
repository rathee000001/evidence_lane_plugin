import {createContext,useContext} from 'react';
import type {StudioSubject} from './subjects';

export type WorkspaceSelection={page:string;section:string;subjects:StudioSubject[];selected:StudioSubject|null;setSection:(section:string)=>void;select:(subject:StudioSubject)=>void;open:(subject:StudioSubject)=>void;publish:(subjects:StudioSubject[])=>void};
export const WorkspaceSelectionContext=createContext<WorkspaceSelection|null>(null);
export const useWorkspaceSelection=()=>useContext(WorkspaceSelectionContext);
