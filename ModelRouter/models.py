# # model_config
class models:
    def __init__(self):
        self._read_only = True
        self._config = {
            "TextOnlyLow" : {
                "Google": {
                    "gemma-3-12b-it" : {
                        "RPM": 30,
                        "TPM": 15000,
                        "RPD": 14400
                    },
                    "gemini-3.1-flash-lite" : {
                        "RPM": 15,
                        "TPM": 250000,
                        "RPD": 500
                    },
                },
                "GitHub" : {
                    "openai/gpt-4o-mini" : {
                        "RPM": 15,
                        "TMP": 8000,
                        "RPD": 150
                    }
                },
                "Ollama" : {
                    "qwen3:4b-instruct": {
                        "RPM": -1,
                        "TMP": -1,
                        "RPD": -1
                    },
                    "deepseek-r1:1.5b": {
                        "RPM": -1,
                        "TMP": -1,
                        "RPD": -1
                    },
                }
            },
            "TextOnlyHigh" : {
                "Google": {
                    "gemini-3-flash" : {
                        "RPM": 5,
                        "TPM": 250000,
                        "RPD": 20
                    },
                    "gemini-2.5-flash-lite" : {
                        "RPM": 10,
                        "TPM": 250000,
                        "RPD": 20
                    },
                    "gemini-2.5-flash" : {
                        "RPM": 5,
                        "TPM": 250000,
                        "RPD": 20
                    },
                },
                "GitHub" : {
                    "openai/gpt-4o" : {
                        "RPM": 10,
                        "TMP": 8000,
                        "RPD": 50
                    },
                    "openai/o1-preview": {
                        "RPM": 1,
                        "TMP": 4000,
                        "RPD": 8
                    },
                    "openai/gpt-5": {
                        "RPM": 1,
                        "TMP": 4000,
                        "RPD": 8
                    },
                    "openai/gpt-5-mini": {
                        "RPM": 2,
                        "TMP": 4000,
                        "RPD": 12
                    },
                    "deepseek/DeepSeek-R1": {
                        "RPM": 1,
                        "TMP": 4000,
                        "RPD": 8    
                    },
                    "xai/grok-3": {
                        "RPM": 1,
                        "TMP": 4000,
                        "RPD": 15    
                    },
                    "xai/grok-3-mini": {
                        "RPM": 2,
                        "TMP": 4000,
                        "RPD": 30    
                    },
                }
            },
            "MultiModal" : {
                "Google": {
                    "gemini-2.5-flash" : {
                        "RPM": 5,
                        "TPM": 250000,
                        "RPD": 20
                    },
                    "gemma-3-27b-it" : {
                        "RPM": 30,
                        "TPM": 15000,
                        "RPD": 14400
                    },
                    "gemini-2.5-flash-tts" : {
                        "RPM": 3,
                        "TPM": 10000,
                        "RPD": 10
                    },
                    "imagen-4-generate" : {
                        "RPM": "N/A",
                        "TPM": "N/A",
                        "RPD": 25
                    },
                    "imagen-4-ultra-generate" : {
                        "RPM": "N/A",
                        "TPM": "N/A",
                        "RPD": 25
                    },
                    "imagen-4-fast-generate" : {
                        "RPM": "N/A",
                        "TPM": "N/A",
                        "RPD": 25
                    },
                }
            }   
        }
    def set_mode(self, value=False):
        self._read_only = value

    def get_models(self, key):
        if key in self._config:
            return self._config[key]
        else:
            return None