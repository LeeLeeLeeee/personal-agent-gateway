export function LoaderCube({ label = "WORKING" }) {
  return (
    <div className="loader">
      <div className="cube-wrap">
        <div className="cube">
          {["f1", "f2", "f3", "f4", "f5", "f6"].map((face) => <div key={face} className={`face ${face}`} />)}
        </div>
      </div>
      <span className="loader-label">{label}...</span>
    </div>
  );
}
