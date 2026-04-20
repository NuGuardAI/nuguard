import { useState } from "react";

const InputField = ({ type, placeholder, icon, value, onChange }) => {
  const [isPasswordShown, setIsPasswordShown] = useState(false);

  return (
    <div className="input-wrapper">
      <input
        type={type === "password" && isPasswordShown ? "text" : type}
        placeholder={placeholder}
        className="input-field"
        required
        value={value}           // controlled value
        onChange={onChange}     // controlled change handler
      />
      <i className="material-symbols-rounded">{icon}</i>

      {type === "password" && (
        <i
          onClick={() => setIsPasswordShown(prev => !prev)}
          className="material-symbols-rounded eye-icon"
          style={{ cursor: "pointer" }}
        >
          {isPasswordShown ? "visibility" : "visibility_off"}
        </i>
      )}
    </div>
  );
};

export default InputField;
