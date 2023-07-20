"""Rail class."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Type

from lxml import etree as ET
from lxml.etree import Element, SubElement
from pydantic import BaseModel
from guardrails import document_store
from guardrails.document_store import DocumentStoreBase

from guardrails.prompt import Instructions, Prompt
from guardrails.schema import JsonSchema, Schema, StringSchema
from guardrails.utils.pydantic_utils import create_xml_element_for_base_model

# TODO: Logging
XMLPARSER = ET.XMLParser(encoding="utf-8")


@dataclass
class Script:
    variables: dict = field(default_factory=dict)
    language: str = "python"
    element: ET._Element = None

    @classmethod
    def from_xml(cls, root: ET._Element) -> "Script":
        if "language" not in root.attrib:
            raise ValueError("Script element must have a language attribute.")

        language = root.attrib["language"]
        if language != "python":
            raise ValueError("Only python scripts are supported right now.")

        # Run the script in the global namespace, returning the additional
        # globals that were created.
        keys = set(globals().keys())
        exec(root.text, globals())
        new_keys = globals().keys()
        variables = {k: globals()[k] for k in new_keys if k not in keys}
        return cls(variables, language, root)

    @staticmethod
    def find_expressions(body) -> List[str]:
        """Get all expressions, written as {...} in a string body."""
        expressions = []
        stack = []
        start = -1

        for i, char in enumerate(body):
            if char == "{":
                if not stack:
                    start = i
                stack.append(char)
            elif char == "}":
                if stack and stack[-1] == "{":
                    stack.pop()
                    if not stack:
                        expressions.append(body[start + 1 : i])
                else:
                    stack.append(char)
        return expressions

    def replace_expressions(self, body: str) -> str:
        """Replace all expressions in a string body with their evaluated
        values."""
        # Decode the body if it's a bytes object.
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        for expr in self.find_expressions(body):
            # The replacement should be inserted as a Python expression, inside
            # curly braces.
            replacement = self(expr)
            # If a string, wrap it in '' quotes.
            if isinstance(replacement, str):
                replacement = f"'{replacement}'"
            body = body.replace(f"{{{expr}}}", f"{{{replacement}}}")

        return body

    def __call__(self, expr: str):
        """Eval expression in the script's namespace."""
        return eval(expr, {**globals(), **self.variables})

    def _to_request(self) -> dict:
        script = None

        if self.element is not None and self.element.text is not None:
            script = {}
            script["text"] = self.element.text

            if self.language is not None:
                script["language"] = self.language

            if self.variables is not None:
                script["variables"] = self.variables

        return script


@dataclass
class Rail:
    """RAIL (Reliable AI Language) is a dialect of XML that allows users to
    specify guardrails for large language models (LLMs).

    A RAIL file contains a root element called
        `<rail version="x.y">`
    that contains the following elements as children:
        1. `<script language="python">`, which contains the script to be executed
        2. `<input strict=True/False>`, which contains the input schema
        3. `<output strict=True/False>`, which contains the output schema
        4. `<prompt>`, which contains the prompt to be passed to the LLM
    """

    document_store: DocumentStoreBase = (None,)
    input_schema: Optional[Schema] = (None,)
    output_schema: Optional[Schema] = (None,)
    instructions: Optional[Instructions] = (None,)
    prompt: Optional[Prompt] = (None,)
    script: Optional[Script] = (None,)
    version: Optional[str] = ("0.1",)


    @classmethod
    def from_pydantic(
        cls, output_class: BaseModel, prompt: str, document_store: DocumentStoreBase, instructions: Optional[str] = None
    ):
        xml = generate_xml_code(output_class, prompt, instructions)
        return cls.from_xml(xml)

    @classmethod
    def from_file(cls, file_path: str, document_store: DocumentStoreBase) -> "Rail":
        with open(file_path, "r") as f:
            xml = f.read()
        return cls.from_string(xml, document_store)

    @classmethod
    def from_string(cls, string: str, document_store: DocumentStoreBase) -> "Rail":
        parsed_xml = ET.fromstring(string, parser=XMLPARSER)
        return cls.from_xml(parsed_xml, document_store)

    @classmethod
    def from_xml(cls, xml: ET._Element, document_store: DocumentStoreBase):
        if "version" not in xml.attrib or xml.attrib["version"] != "0.1":
            raise ValueError(
                "RAIL file must have a version attribute set to 0.1."
                "Change the opening <rail> element to: <rail version='0.1'>."
            )

        # Execute the script before validating the rest of the RAIL file.
        raw_script = xml.find("script")
        print("here 0")
        if raw_script is not None:
            script = cls.load_script(raw_script)
        else:
            script = Script()

        # Load <input /> schema
        raw_input_schema = xml.find("input")
        if raw_input_schema is None:
            # No input schema, so do no input checking.
            input_schema = Schema(document_store=document_store)
        else:
            input_schema = cls.load_input_schema(raw_input_schema, document_store)
        print("here 1")
        # Load <output /> schema
        raw_output_schema = xml.find("output")
        if raw_output_schema is None:
            raise ValueError("RAIL file must contain a output-schema element.")
        # Replace all expressions in the <output /> schema.
        raw_output_schema = script.replace_expressions(ET.tostring(raw_output_schema))
        raw_output_schema = ET.fromstring(raw_output_schema, parser=XMLPARSER)
        print("here 1.5")
        output_schema = cls.load_output_schema(raw_output_schema, document_store)
        print("here 2")
        # Parse instructions for the LLM. These are optional but if given,
        # LLMs can use them to improve their output. Commonly these are
        # prepended to the prompt.
        instructions = xml.find("instructions")
        if instructions is not None:
            instructions = cls.load_instructions(instructions, output_schema)
        print("here 3")

        # Load <prompt />
        prompt = xml.find("prompt")
        if prompt is None:
            raise ValueError("RAIL file must contain a prompt element.")
        prompt = cls.load_prompt(prompt, output_schema)


        return cls(
            document_store=document_store,
            input_schema=input_schema,
            output_schema=output_schema,
            instructions=instructions,
            prompt=prompt,
            script=script,
            version=xml.attrib["version"],
        )

    @staticmethod
    def load_schema(root: ET._Element, document_store: DocumentStoreBase) -> Schema:
        """Given the RAIL <input> or <output> element, create a Schema
        object."""
        return Schema(document_store=document_store, root=root)

    @staticmethod
    def load_input_schema(root: ET._Element, document_store: DocumentStoreBase) -> Schema:
        """Given the RAIL <input> element, create a Schema object."""
        # Recast the schema as an InputSchema.
        return Schema(document_store=document_store, root=root)

    @staticmethod
    def load_output_schema(root: ET._Element, document_store: DocumentStoreBase) -> Schema:
        """Given the RAIL <output> element, create a Schema object."""
        print(document_store)
        # If root contains a `type="string"` attribute, then it's a StringSchema
        if "type" in root.attrib and root.attrib["type"] == "string":
            print("if")
            return StringSchema(root=root, document_store=document_store)
        print("else")
        return JsonSchema(root, document_store)

    @staticmethod
    def load_instructions(root: ET._Element, output_schema: Schema) -> Instructions:
        """Given the RAIL <instructions> element, create Instructions."""
        return Instructions(
            source=root.text,
            output_schema=output_schema.transpile(),
        )

    @staticmethod
    def load_prompt(root: ET._Element, output_schema: Schema) -> Prompt:
        print("loaad prompt")
        print(root)
        print (output_schema)
        """Given the RAIL <prompt> element, create a Prompt object."""
        return Prompt(
            source=root.text,
            output_schema=output_schema.transpile(),
        )

    @staticmethod
    def load_script(root: ET._Element) -> Script:
        """Given the RAIL <script> element, load and execute the script."""
        return Script.from_xml(root)

    def _to_request(self) -> Dict:
        rail = {"version": self.version}

        input_schema = (
            self.input_schema._to_request() if self.input_schema is not None else None
        )
        if input_schema is not None:
            rail["inputSchema"] = input_schema
        output_schema = (
            self.output_schema._to_request() if self.output_schema is not None else None
        )
        if output_schema is not None:
            rail["outputSchema"] = output_schema
        if self.instructions is not None:
            rail["instructions"] = self.instructions._to_request()
        if self.prompt is not None:
            rail["prompt"] = self.prompt._to_request()
        if (
            self.script is not None
            and self.script.element is not None
            and self.script.element.text is not None
        ):
            rail["script"] = self.script._to_request()
        return rail


def generate_xml_code(
    output_class: Type[BaseModel],
    prompt: str,
    instructions: Optional[str] = None,
) -> ET._Element:
    """Generate XML RAIL Spec from a pydantic model and a prompt."""

    # Create the root element
    root = Element("rail")
    root.set("version", "0.1")

    # Create the output element
    output_element = SubElement(root, "output")

    # Create XML elements for the output_class
    create_xml_element_for_base_model(output_class, output_element)

    # Create the prompt element
    prompt_element = SubElement(root, "prompt")
    prompt_text = f"{prompt}"
    prompt_element.text = prompt_text

    if instructions is not None:
        # Create the instructions element
        instructions_element = SubElement(root, "instructions")
        instructions_text = f"{instructions}"
        instructions_element.text = instructions_text

    return root
