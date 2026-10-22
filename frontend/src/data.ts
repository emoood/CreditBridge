export type ApplicationStatus = 'Needs review' | 'Awaiting chair' | 'Missing document' | 'Processing' | 'Completed';
export type Application = {id: string; student: string; institution: string; program: string; courses: number; submitted: string; status: ApplicationStatus; note: string; priority?: boolean};
export const applications: Application[] = [
  {id:'TR-2026-024',student:'Sara Ahmed',institution:'University of Sharjah',program:'Computer Engineering',courses:6,submitted:'22 Oct 2026',status:'Needs review',note:'AI flagged a course content mismatch',priority:true},
  {id:'TR-2026-021',student:'Omar Hassan',institution:'University of Birmingham',program:'Business Administration',courses:5,submitted:'21 Oct 2026',status:'Missing document',note:'Source syllabus required for ACCG 201',priority:true},
  {id:'TR-2026-018',student:'Maya Ibrahim',institution:'American University of Beirut',program:'Architecture',courses:8,submitted:'20 Oct 2026',status:'Awaiting chair',note:'3 courses waiting for department approval',priority:true},
  {id:'TR-2026-017',student:'Lina Farah',institution:'University of Wollongong',program:'Business Administration',courses:4,submitted:'19 Oct 2026',status:'Completed',note:'Evaluation completed'},
  {id:'TR-2026-016',student:'Yusuf Khan',institution:'Heriot-Watt University',program:'Computer Science',courses:7,submitted:'19 Oct 2026',status:'Processing',note:'Matching agent evaluating course outcomes'},
  {id:'TR-2026-014',student:'Noor Ali',institution:'University of Toronto',program:'Interior Design',courses:3,submitted:'18 Oct 2026',status:'Completed',note:'Evaluation completed'}
];
export const agentEvents = [
  {label:'Transcript extracted',detail:'6 courses identified',done:true},
  {label:'Candidates retrieved',detail:'14 relevant catalog entries',done:true},
  {label:'Matching courses',detail:'Comparing academic content and outcomes',done:false,active:true},
  {label:'Independent review',detail:'Checks evidence and weak matches',done:false}
];
